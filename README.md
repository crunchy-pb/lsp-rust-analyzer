# rust-analyzer Container

Production container packaging for the official `rust-analyzer` language server for use with `pb` container integrations.

## Build

```sh
docker build \
  --build-arg INTEGRATION_CONFIG_SCHEMA="$(npm run --silent config-schema:label)" \
  -t rust-analyzer:local .
```

The runtime pins Debian and rust-analyzer versions in the Docker build arguments. The default rust-analyzer release is `2026-06-29` (`v0.3.2955`).

## Run

`rust-analyzer` speaks Language Server Protocol over standard input/output, so run it with an interactive stdin:

```sh
docker run --rm -i \
  -v "$PWD:/workspace" \
  -w /workspace \
  rust-analyzer:local
```

The image includes `cargo`, `rustc`, `rustfmt`, and `clippy` from Debian so rust-analyzer can discover workspaces, run diagnostics, and format code without an additional toolchain mount.

## Container metadata

The image publishes `pb` discovery metadata using OCI/Docker labels:

* `uk.unrtd.pb.integration.name` identifies the integration as `io.github.rust-lang/rust-analyzer`.
* `uk.unrtd.pb.integration.config-schema` contains the compact JSON Schema from `config.schema.json`, describing supported environment variables.

Use `config.schema.json` as the readable source for the embedded label when updating supported environment variables. The `config-schema:label` npm script compacts that schema for Docker build arguments used by CI and release builds.

## Releases

Semantic release publishes GitHub releases from `main`. When a new release is created, CI builds and pushes the container image to GitHub Container Registry with both the release version and `latest` tags.
