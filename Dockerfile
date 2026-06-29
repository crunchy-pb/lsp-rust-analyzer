# syntax=docker/dockerfile:1.7
ARG DEBIAN_VERSION=bookworm-slim
ARG RUST_ANALYZER_VERSION=2026-06-29
ARG RUST_ANALYZER_ARCH=x86_64-unknown-linux-gnu

FROM debian:${DEBIAN_VERSION} AS downloader
ARG RUST_ANALYZER_VERSION
ARG RUST_ANALYZER_ARCH
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL \
      "https://github.com/rust-lang/rust-analyzer/releases/download/${RUST_ANALYZER_VERSION}/rust-analyzer-${RUST_ANALYZER_ARCH}.gz" \
      -o /tmp/rust-analyzer.gz \
    && gunzip /tmp/rust-analyzer.gz \
    && chmod 0755 /tmp/rust-analyzer

FROM debian:${DEBIAN_VERSION} AS runtime
ARG INTEGRATION_CONFIG_SCHEMA="{}"
ARG RUST_ANALYZER_VERSION
LABEL org.opencontainers.image.title="rust-analyzer" \
      org.opencontainers.image.description="Container packaging for the rust-analyzer language server" \
      org.opencontainers.image.source="https://github.com/rust-lang/rust-analyzer" \
      org.opencontainers.image.licenses="MIT OR Apache-2.0" \
      org.opencontainers.image.version="${RUST_ANALYZER_VERSION}" \
      uk.unrtd.pb.integration.name="io.github.rust-lang/rust-analyzer" \
      uk.unrtd.pb.integration.config-schema="${INTEGRATION_CONFIG_SCHEMA}"
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates cargo rustc rustfmt clippy \
    && rm -rf /var/lib/apt/lists/*
COPY --from=downloader /tmp/rust-analyzer /usr/local/bin/rust-analyzer
RUN useradd --create-home --shell /usr/sbin/nologin rust-analyzer
USER rust-analyzer
WORKDIR /workspace
ENTRYPOINT ["rust-analyzer"]
