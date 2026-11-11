FROM debian:trixie-slim@sha256:28de0877c2189802884ccd20f15ee41c203573bd87bb6b883f5f46362d24c5c2 AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 build-essential meson ninja-build pkg-config libcap-dev patch \
    && rm -rf /var/lib/apt/lists/*

FROM debian:trixie-slim@sha256:28de0877c2189802884ccd20f15ee41c203573bd87bb6b883f5f46362d24c5c2 AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends python3 libcap2 \
    && rm -rf /var/lib/apt/lists/*
