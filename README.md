# pb rust-analyzer LSP package

This repository packages `rust-analyzer` for pb's proactive, deterministic LSP pass. The image is deliberately syntax-first: it can report parser diagnostics as soon as a Rust document is opened, while Cargo checks, build scripts, procedural macros, cache priming, dependency traversal, and network access remain disabled.

The canonical image is `ghcr.io/crunchy-pb/lsp-rust-analyzer`.

## Safety profile

[`pb-lsp.json`](pb-lsp.json) is the typed package manifest and the single readable source for the embedded `uk.unrtd.pb.integration.lsp-manifest` label. It fixes the following behavior:

- language ID: `rust` only;
- workspace access: read-only;
- network access: none;
- server arguments and cache IDs: empty;
- check-on-save, build scripts, procedural macros, Cargo autoreload, and cache priming: disabled;
- Cargo dependency traversal: disabled with `cargo.noDeps=true`.

[`config.schema.json`](config.schema.json) is the single readable source for the `uk.unrtd.pb.integration.config-schema` label. Its strict empty-object schema exposes no environment configuration and rejects additional properties. The package has no credential, network, logging, or Cargo-path settings.

The runtime uses UID/GID `65532:65532`, an immutable root filesystem and workspace, and a writable `/tmp` tmpfs. `CARGO_NET_OFFLINE=true` prevents Cargo network use even if the runtime isolation is accidentally relaxed.

## Fixed toolchain

The image copies the rustup toolchain from the pinned `rust:1.96.0-slim-bookworm` builder into a pinned Debian Bookworm runtime. It includes the Rust `1.96.0` `rust-analyzer` and `rust-src` components and does not download a standalone binary.

This fixed toolchain is intentional for reproducibility. Projects that require another Rust toolchain may have incomplete or inaccurate semantic information. The package still targets early syntax diagnostics; use a separately controlled build or project-specific analysis pass when exact toolchain semantics are required.

## Validate, build, and smoke test

The local image name is `pb/lsp-rust-analyzer:dev`. Docker is the default runtime:

```sh
./scripts/validate-package.py
./scripts/build-local.sh
./scripts/smoke.py
./scripts/smoke.py
```

The equivalent explicit Docker form sets `CONTAINER_RUNTIME=docker` for both scripts.

On Apple silicon with Apple's `container` CLI:

```sh
./scripts/validate-package.py
CONTAINER_RUNTIME=container ./scripts/build-local.sh
CONTAINER_RUNTIME=container ./scripts/smoke.py
CONTAINER_RUNTIME=container ./scripts/smoke.py
```

`build-local.sh` compacts `pb-lsp.json` and `config.schema.json`, supplies both as required Docker build arguments, builds the image, and verifies its labels, user, entrypoint, and offline environment. `smoke.py` creates a temporary internal network, runs with a read-only root and `/workspace`, uses tmpfs for `/tmp`, and requires a real non-empty LSP syntax diagnostic from the invalid Rust fixture. The test applies protocol and process timeouts and always removes its container and network.

Install the locally built package into pb with the typed manifest:

```sh
pb integrations add lsp pb/lsp-rust-analyzer:dev \
  --name rust-analyzer \
  --manifest ./pb-lsp.json
```

## Releases and images

Pull requests and pushes validate the locked safety profile, install release dependencies with `npm ci --ignore-scripts`, build and load a real image, inspect its metadata, and run the isolated LSP smoke test twice. Publishing is gated on those checks.

Semantic Release retains the existing `v<major>.<minor>.<patch>` Git tags and GitHub releases. A successful release publishes a multi-architecture `linux/amd64` and `linux/arm64` image with an SBOM and maximum build provenance under these tags:

- `latest`;
- the full semantic version, such as `2.3.4`;
- the compatible major and major/minor tags, such as `2` and `2.3`;
- the release commit tag, such as `sha-0123456789ab`.

The source repository is licensed under the [MIT License](LICENSE). Rust, rustup, rust-analyzer, rust-src, and other copied toolchain components retain their upstream licenses, including the Rust project's MIT and Apache-2.0 terms.
