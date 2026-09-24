# syntax=docker/dockerfile:1.7

FROM rust:1.98.1-slim-bookworm@sha256:ff521445a372125ed4f76e1453a1f8098f2d05332d1601d30db1c1f62757e730 AS toolchain

RUN rustup component add --toolchain 1.96.0 rust-analyzer rust-src

FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818 AS runtime

ARG PB_LSP_MANIFEST
ARG INTEGRATION_CONFIG_SCHEMA
ARG IMAGE_VERSION=1.96.0

RUN test -n "${PB_LSP_MANIFEST}" \
    && test -n "${INTEGRATION_CONFIG_SCHEMA}" \
    && groupadd --gid 65532 pb \
    && useradd --uid 65532 --gid 65532 --no-create-home \
         --home-dir /tmp/pb-home --shell /usr/sbin/nologin pb \
    && install --directory --owner=65532 --group=65532 /workspace

COPY --from=toolchain /usr/local/rustup /usr/local/rustup
COPY --from=toolchain /usr/local/cargo /usr/local/cargo

ENV PATH=/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin \
    RUSTUP_HOME=/usr/local/rustup \
    RUSTUP_TOOLCHAIN=1.96.0 \
    CARGO_HOME=/tmp/cargo \
    CARGO_NET_OFFLINE=true \
    HOME=/tmp/pb-home

LABEL org.opencontainers.image.title="pb rust-analyzer LSP" \
      org.opencontainers.image.description="Deterministic, offline, syntax-first Rust language server for pb" \
      org.opencontainers.image.source="https://github.com/crunchy-pb/lsp-rust-analyzer" \
      org.opencontainers.image.licenses="MIT OR Apache-2.0" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      uk.unrtd.pb.integration.name="io.github.rust-lang/rust-analyzer" \
      uk.unrtd.pb.integration.lsp-manifest="${PB_LSP_MANIFEST}" \
      uk.unrtd.pb.integration.config-schema="${INTEGRATION_CONFIG_SCHEMA}"

USER 65532:65532
WORKDIR /workspace
ENTRYPOINT ["rust-analyzer"]
