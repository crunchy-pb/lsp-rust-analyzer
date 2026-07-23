#!/usr/bin/env python3
"""Validate the package's locked-down sources and optional built image metadata."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "pb-lsp.json"
SCHEMA_PATH = ROOT / "config.schema.json"
DOCKERFILE_PATH = ROOT / "Dockerfile"
MAX_LABEL_BYTES = 64 * 1024

LSP_LABEL = "uk.unrtd.pb.integration.lsp-manifest"
SCHEMA_LABEL = "uk.unrtd.pb.integration.config-schema"

EXPECTED_MANIFEST = {
    "version": 1,
    "kind": "lsp",
    "server": {
        "args": [],
        "language_ids": ["rust"],
        "initialization_options": {
            "cachePriming": {"enable": False},
            "cargo": {
                "autoreload": False,
                "buildScripts": {"enable": False},
                "noDeps": True,
            },
            "checkOnSave": False,
            "procMacro": {"enable": False},
        },
        "workspace_access": "read_only",
        "network_access": "none",
        "cache_ids": [],
    },
}

EXPECTED_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "rust-analyzer",
    "description": (
        "This package runs offline and does not require credentials or "
        "environment configuration."
    ),
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

PINNED_BASES = (
    "rust:1.96.0-slim-bookworm@sha256:"
    "4732ca96fd086cb9be682050c3f0176288eebaac2b80aa2bcefccfaf198e1950",
    "debian:bookworm-slim@sha256:"
    "7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818",
)


class ValidationError(RuntimeError):
    """Raised when package metadata violates the fixed safety profile."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError(f"cannot parse {path.relative_to(ROOT)}: {error}") from error


def compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def validate_sources() -> Dict[str, Any]:
    manifest = read_json(MANIFEST_PATH)
    schema = read_json(SCHEMA_PATH)

    if manifest != EXPECTED_MANIFEST:
        raise ValidationError("pb-lsp.json does not match the approved LSP safety profile")
    if schema != EXPECTED_SCHEMA:
        raise ValidationError("config.schema.json is not the strict empty environment schema")

    source_size = len(MANIFEST_PATH.read_bytes())
    compact_size = len(compact_json(manifest).encode("utf-8"))
    if source_size >= MAX_LABEL_BYTES or compact_size >= MAX_LABEL_BYTES:
        raise ValidationError("pb-lsp.json must remain below 64 KiB")

    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    for label in (LSP_LABEL, SCHEMA_LABEL):
        if not re.search(rf"(?m)^\s*{re.escape(label)}=", dockerfile):
            raise ValidationError(f"Dockerfile is missing label {label}")
    for image in PINNED_BASES:
        if not re.search(rf"(?m)^FROM\s+{re.escape(image)}(?:\s|$)", dockerfile):
            raise ValidationError(f"Dockerfile is missing pinned base {image}")
    for build_arg in ("PB_LSP_MANIFEST", "INTEGRATION_CONFIG_SCHEMA"):
        if not re.search(rf"(?m)^ARG\s+{build_arg}\s*$", dockerfile):
            raise ValidationError(f"Dockerfile must require build argument {build_arg}")
        if f'test -n "${{{build_arg}}}"' not in dockerfile:
            raise ValidationError(f"Dockerfile must reject an empty {build_arg}")

    return {"manifest": manifest, "schema": schema}


def run_inspect(runtime: str, image: str) -> Any:
    command = [runtime, "image", "inspect", image]
    try:
        result = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as error:
        raise ValidationError(f"container runtime not found: {runtime}") from error
    except subprocess.TimeoutExpired as error:
        raise ValidationError(f"timed out inspecting image {image}") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip()[-4096:]
        raise ValidationError(f"cannot inspect image {image}: {detail}") from error

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValidationError(f"{runtime} image inspect did not return JSON") from error


def normalized_mapping(value: Mapping[str, Any]) -> Dict[str, Any]:
    return {str(key).lower(): item for key, item in value.items()}


def candidate_configs(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        normalized = normalized_mapping(value)
        if "labels" in normalized and any(
            key in normalized for key in ("user", "entrypoint", "env")
        ):
            yield value
        for child in value.values():
            yield from candidate_configs(child)
    elif isinstance(value, list):
        for child in value:
            yield from candidate_configs(child)


def as_string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise ValidationError("image metadata contains an invalid string list")


def inspect_image(runtime: str, image: str, sources: Mapping[str, Any]) -> None:
    metadata = run_inspect(runtime, image)
    configs = list(candidate_configs(metadata))
    if not configs:
        raise ValidationError("could not locate OCI runtime configuration in image metadata")

    for candidate in configs:
        config = normalized_mapping(candidate)
        labels_value = config.get("labels")
        if not isinstance(labels_value, Mapping):
            continue
        labels = {str(key): value for key, value in labels_value.items()}
        if LSP_LABEL not in labels or SCHEMA_LABEL not in labels:
            continue

        try:
            embedded_manifest = json.loads(labels[LSP_LABEL])
            embedded_schema = json.loads(labels[SCHEMA_LABEL])
        except (TypeError, json.JSONDecodeError) as error:
            raise ValidationError("embedded pb labels must decode as JSON") from error

        if embedded_manifest != sources["manifest"]:
            raise ValidationError("embedded LSP manifest differs from pb-lsp.json")
        if embedded_schema != sources["schema"]:
            raise ValidationError("embedded config schema differs from config.schema.json")

        if str(config.get("user", "")) != "65532:65532":
            raise ValidationError("image user must be 65532:65532")
        if as_string_list(config.get("entrypoint")) != ["rust-analyzer"]:
            raise ValidationError("image entrypoint must be rust-analyzer")
        if "CARGO_NET_OFFLINE=true" not in as_string_list(config.get("env")):
            raise ValidationError("image must set CARGO_NET_OFFLINE=true")
        return

    raise ValidationError("built image is missing one or both required pb labels")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="also inspect this locally loaded image")
    parser.add_argument(
        "--runtime",
        default=os.environ.get("CONTAINER_RUNTIME", "docker"),
        help="container CLI used with --image (default: CONTAINER_RUNTIME or docker)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        sources = validate_sources()
        if args.image:
            inspect_image(args.runtime, args.image, sources)
    except ValidationError as error:
        print(f"validation failed: {error}", file=sys.stderr)
        return 1

    scope = "sources and image metadata" if args.image else "package sources"
    print(f"validated {scope}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
