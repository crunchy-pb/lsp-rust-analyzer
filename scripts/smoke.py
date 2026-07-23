#!/usr/bin/env python3
"""Run a bounded, network-isolated LSP syntax-diagnostics smoke test."""

import argparse
from collections import deque
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import threading
import time
from typing import Any, Deque, Dict, List, Mapping, Optional, Sequence, Tuple
import uuid


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "test/fixtures/syntax-error"
SOURCE = FIXTURE / "src/main.rs"
MANIFEST = ROOT / "pb-lsp.json"
MAX_HEADER_BYTES = 8 * 1024
MAX_FRAME_BYTES = 1024 * 1024
STDERR_TAIL_BYTES = 64 * 1024
DEFAULT_TIMEOUT = 30.0


class SmokeError(RuntimeError):
    """Raised for bounded protocol, process, or assertion failures."""


class StderrTail:
    def __init__(self, stream: Any, limit: int) -> None:
        self._stream = stream
        self._limit = limit
        self._chunks: Deque[bytes] = deque()
        self._size = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._drain, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _drain(self) -> None:
        while True:
            chunk = self._stream.read(4096)
            if not chunk:
                return
            with self._lock:
                self._chunks.append(chunk)
                self._size += len(chunk)
                while self._size > self._limit and self._chunks:
                    removed = self._chunks.popleft()
                    self._size -= len(removed)

    def text(self) -> str:
        with self._lock:
            tail = b"".join(self._chunks)[-self._limit :]
        return tail.decode("utf-8", errors="replace")

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)


class LspSession:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        if process.stdin is None or process.stdout is None:
            raise SmokeError("container process pipes were not created")
        self.process = process
        self.stdin = process.stdin
        self.stdout = process.stdout
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.stdout, selectors.EVENT_READ)
        self.buffer = bytearray()

    def send(self, message: Mapping[str, Any]) -> None:
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_FRAME_BYTES:
            raise SmokeError("attempted to send an oversized LSP frame")
        frame = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        try:
            self.stdin.write(frame)
            self.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise SmokeError("rust-analyzer closed stdin unexpectedly") from error

    def _fill(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SmokeError("timed out waiting for an LSP frame")
        if not self.selector.select(remaining):
            raise SmokeError("timed out waiting for an LSP frame")
        chunk = os.read(self.stdout.fileno(), 4096)
        if not chunk:
            code = self.process.poll()
            raise SmokeError(f"rust-analyzer stdout closed (exit code {code})")
        self.buffer.extend(chunk)

    def receive(self, deadline: float) -> Dict[str, Any]:
        delimiter = b"\r\n\r\n"
        while delimiter not in self.buffer:
            if len(self.buffer) > MAX_HEADER_BYTES:
                raise SmokeError("LSP headers exceed the 8 KiB limit")
            self._fill(deadline)

        header_end = self.buffer.index(delimiter)
        if header_end > MAX_HEADER_BYTES:
            raise SmokeError("LSP headers exceed the 8 KiB limit")
        header = bytes(self.buffer[:header_end]).decode("ascii", errors="strict")
        del self.buffer[: header_end + len(delimiter)]

        content_length: Optional[int] = None
        for line in header.split("\r\n"):
            name, separator, value = line.partition(":")
            if not separator:
                raise SmokeError("malformed LSP header")
            if name.strip().lower() == "content-length":
                try:
                    content_length = int(value.strip())
                except ValueError as error:
                    raise SmokeError("invalid LSP Content-Length") from error
        if content_length is None:
            raise SmokeError("LSP frame is missing Content-Length")
        if content_length < 0 or content_length > MAX_FRAME_BYTES:
            raise SmokeError("LSP frame exceeds the 1 MiB limit")

        while len(self.buffer) < content_length:
            self._fill(deadline)
        body = bytes(self.buffer[:content_length])
        del self.buffer[:content_length]
        try:
            message = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SmokeError("received invalid JSON from rust-analyzer") from error
        if not isinstance(message, dict):
            raise SmokeError("received a non-object JSON-RPC message")
        return message


def run_quiet(command: Sequence[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise SmokeError(f"container runtime not found: {command[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise SmokeError(f"command timed out: {' '.join(command)}") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip()[-4096:]
        raise SmokeError(f"command failed: {' '.join(command)}\n{detail}") from error


def cleanup(runtime: str, container_name: str, network_name: str) -> None:
    commands: List[List[str]]
    if Path(runtime).name == "docker":
        commands = [
            [runtime, "rm", "--force", container_name],
            [runtime, "network", "rm", network_name],
        ]
    else:
        commands = [
            [runtime, "rm", "--force", container_name],
            [runtime, "network", "delete", network_name],
        ]
    for command in commands:
        try:
            subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass


def reply_to_server_request(
    session: LspSession,
    message: Mapping[str, Any],
    initialization_options: Mapping[str, Any],
    workspace_folders: List[Dict[str, str]],
) -> bool:
    if "id" not in message or not isinstance(message.get("method"), str):
        return False

    method = message["method"]
    if method == "workspace/configuration":
        params = message.get("params")
        items = params.get("items", []) if isinstance(params, dict) else []
        result: Any = [initialization_options for _ in items]
    elif method == "workspace/workspaceFolders":
        result = workspace_folders
    elif method in ("client/registerCapability", "window/workDoneProgress/create"):
        result = None
    else:
        session.send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {"code": -32601, "message": f"unsupported request: {method}"},
            }
        )
        return True

    session.send({"jsonrpc": "2.0", "id": message["id"], "result": result})
    return True


def wait_for(
    session: LspSession,
    deadline: float,
    predicate: Any,
    initialization_options: Mapping[str, Any],
    workspace_folders: List[Dict[str, str]],
) -> Dict[str, Any]:
    while True:
        message = session.receive(deadline)
        if reply_to_server_request(
            session, message, initialization_options, workspace_folders
        ):
            continue
        if predicate(message):
            return message


def container_commands(
    runtime: str, image: str, container_name: str, network_name: str
) -> Tuple[List[str], List[str]]:
    runtime_name = Path(runtime).name
    if runtime_name not in ("docker", "container"):
        raise SmokeError("CONTAINER_RUNTIME must name docker or container")
    network = [runtime, "network", "create", "--internal", network_name]

    run = [
        runtime,
        "run",
        "--rm",
        "--interactive",
        "--name",
        container_name,
        "--network",
        network_name,
        "--read-only",
        "--volume",
        f"{FIXTURE}:/workspace:ro",
        "--tmpfs",
        "/tmp",
        "--workdir",
        "/workspace",
        image,
    ]
    return network, run


def smoke(runtime: str, image: str, timeout: float) -> None:
    if not SOURCE.is_file():
        raise SmokeError(f"fixture source is missing: {SOURCE}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    initialization_options = manifest["server"]["initialization_options"]
    source_text = SOURCE.read_text(encoding="utf-8")
    source_uri = "file:///workspace/src/main.rs"
    root_uri = "file:///workspace"
    workspace_folders = [{"uri": root_uri, "name": "syntax-error"}]

    suffix = uuid.uuid4().hex[:12]
    container_name = f"pb-ra-smoke-{suffix}"
    network_name = f"pb-ra-smoke-{suffix}"
    network_command, run_command = container_commands(
        runtime, image, container_name, network_name
    )

    process: Optional[subprocess.Popen[bytes]] = None
    stderr_tail: Optional[StderrTail] = None
    try:
        run_quiet(network_command)
        process = subprocess.Popen(
            run_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.stderr is None:
            raise SmokeError("container stderr pipe was not created")
        stderr_tail = StderrTail(process.stderr, STDERR_TAIL_BYTES)
        stderr_tail.start()
        session = LspSession(process)
        deadline = time.monotonic() + timeout

        session.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "processId": None,
                    "clientInfo": {"name": "pb-lsp-smoke", "version": "1"},
                    "rootUri": root_uri,
                    "capabilities": {
                        "workspace": {
                            "configuration": True,
                            "workspaceFolders": True,
                        }
                    },
                    "initializationOptions": initialization_options,
                    "workspaceFolders": workspace_folders,
                },
            }
        )
        initialize_response = wait_for(
            session,
            deadline,
            lambda message: message.get("id") == 1,
            initialization_options,
            workspace_folders,
        )
        if "error" in initialize_response:
            raise SmokeError(f"initialize failed: {initialize_response['error']}")

        session.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        session.send(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": source_uri,
                        "languageId": "rust",
                        "version": 1,
                        "text": source_text,
                    }
                },
            }
        )

        diagnostics_message = wait_for(
            session,
            deadline,
            lambda message: (
                message.get("method") == "textDocument/publishDiagnostics"
                and isinstance(message.get("params"), dict)
                and message["params"].get("uri") == source_uri
                and isinstance(message["params"].get("diagnostics"), list)
                and len(message["params"]["diagnostics"]) > 0
            ),
            initialization_options,
            workspace_folders,
        )
        diagnostics = diagnostics_message["params"]["diagnostics"]
        diagnostic_count = len(diagnostics)
        if not any(
            isinstance(diagnostic, dict) and diagnostic.get("code") == "syntax-error"
            for diagnostic in diagnostics
        ):
            raise SmokeError("publishDiagnostics did not contain a syntax-error diagnostic")
        diagnostic_codes = sorted(
            {
                str(diagnostic.get("code"))
                for diagnostic in diagnostics
                if isinstance(diagnostic, dict) and diagnostic.get("code") is not None
            }
        )

        session.send({"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": None})
        shutdown_response = wait_for(
            session,
            deadline,
            lambda message: message.get("id") == 2,
            initialization_options,
            workspace_folders,
        )
        if "error" in shutdown_response:
            raise SmokeError(f"shutdown failed: {shutdown_response['error']}")
        session.send({"jsonrpc": "2.0", "method": "exit", "params": None})
        remaining = max(0.1, deadline - time.monotonic())
        try:
            exit_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise SmokeError("rust-analyzer did not exit after shutdown") from error
        if exit_code != 0:
            raise SmokeError(f"rust-analyzer exited with code {exit_code}")

        code_summary = ", ".join(diagnostic_codes) if diagnostic_codes else "unclassified"
        print(
            f"smoke test passed with {diagnostic_count} syntax diagnostic(s) "
            f"(codes: {code_summary})"
        )
    except Exception as error:
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if stderr_tail is not None:
            stderr_tail.join(1)
        detail = stderr_tail.text().strip() if stderr_tail is not None else ""
        if detail:
            raise SmokeError(f"{error}\n--- bounded stderr tail ---\n{detail}") from error
        if isinstance(error, SmokeError):
            raise
        raise SmokeError(str(error)) from error
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        cleanup(runtime, container_name, network_name)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        default=os.environ.get("IMAGE_NAME", "pb/lsp-rust-analyzer:dev"),
        help="local image to test",
    )
    parser.add_argument(
        "--runtime",
        default=os.environ.get("CONTAINER_RUNTIME", "docker"),
        help="docker-compatible CLI (default: CONTAINER_RUNTIME or docker)",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.timeout <= 0 or args.timeout > 120:
        print("smoke test failed: timeout must be between 0 and 120 seconds", file=sys.stderr)
        return 2
    try:
        smoke(args.runtime, args.image, args.timeout)
    except SmokeError as error:
        print(f"smoke test failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
