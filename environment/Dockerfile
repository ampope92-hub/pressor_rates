# The audit tool's box. The committee's tooling is TypeScript run under node, with node's own
# modules and nothing else, so node and the TypeScript compiler are the whole toolchain. The
# week's messages are not in this image: they live in the engine service's image.
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        nodejs \
        ca-certificates \
        curl \
        tmux \
        asciinema \
    && rm -rf /var/lib/apt/lists/*

# TypeScript and the node type declarations from the registry tarballs, pinned by version and
# checksum, without npm.
RUN curl -fsSL -o /tmp/typescript.tgz https://registry.npmjs.org/typescript/-/typescript-5.6.3.tgz \
    && echo "ef67f8d8ad895858024b7339d3e34bf112cae3c5db1f538c3079038b17ae30fa  /tmp/typescript.tgz" | sha256sum -c - \
    && mkdir -p /usr/local/lib/node_modules/typescript \
    && tar -xzf /tmp/typescript.tgz -C /usr/local/lib/node_modules/typescript --strip-components=1 \
    && ln -s /usr/local/lib/node_modules/typescript/bin/tsc /usr/local/bin/tsc \
    && rm -f /tmp/typescript.tgz
RUN curl -fsSL -o /tmp/types-node.tgz https://registry.npmjs.org/@types/node/-/node-18.19.130.tgz \
    && echo "30e36412dac091a634407127bea2c712c55de136e33c2d234b1529bb876418ca  /tmp/types-node.tgz" | sha256sum -c - \
    && curl -fsSL -o /tmp/undici-types.tgz https://registry.npmjs.org/undici-types/-/undici-types-5.26.5.tgz \
    && echo "503bffdea1bee0526fb2ac75a4ac01ee5e712f251fabdeb8da4b408a519ed465  /tmp/undici-types.tgz" | sha256sum -c - \
    && mkdir -p /usr/local/lib/node_modules/@types/node /usr/local/lib/node_modules/undici-types \
    && tar -xzf /tmp/types-node.tgz -C /usr/local/lib/node_modules/@types/node --strip-components=1 \
    && tar -xzf /tmp/undici-types.tgz -C /usr/local/lib/node_modules/undici-types --strip-components=1 \
    && rm -f /tmp/types-node.tgz /tmp/undici-types.tgz

# Where the engine is. The grader points the tool somewhere else, so nothing may be built in.
ENV ENGINE_HOST=127.0.0.1 \
    ENGINE_PORT=7710

COPY app/audit /app/audit
RUN chmod +x /app/audit/build.sh
WORKDIR /app/audit
