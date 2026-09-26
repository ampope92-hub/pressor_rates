# Verifier image. Runs in its own container and sees only /app/audit/src from the agent's box. It
# holds what the agent never sees: the three weeks, the report each must produce, and the engine
# that serves them. It compiles the collected TypeScript itself and runs the tool against an
# engine it starts, so the report has to come out of the agent's program talking the protocol.

# The tool is started through tests/launch.c, which lets it start no other program. Built
# statically in a throwaway stage, so no C compiler reaches the final image.
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb AS launcher
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
COPY launch.c /tmp/launch.c
RUN gcc -O2 -static -Wall -o /tool_launch /tmp/launch.c

FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# The same compiler and type declarations the agent's box carries, pinned the same way.
RUN curl -fsSL -o /tmp/typescript.tgz https://registry.npmjs.org/typescript/-/typescript-5.6.3.tgz \
    && echo "ef67f8d8ad895858024b7339d3e34bf112cae3c5db1f538c3079038b17ae30fa  /tmp/typescript.tgz" | sha256sum -c - \
    && mkdir -p /usr/local/lib/node_modules/typescript \
    && tar -xzf /tmp/typescript.tgz -C /usr/local/lib/node_modules/typescript --strip-components=1 \
    && rm -f /tmp/typescript.tgz
RUN curl -fsSL -o /tmp/types-node.tgz https://registry.npmjs.org/@types/node/-/node-18.19.130.tgz \
    && echo "30e36412dac091a634407127bea2c712c55de136e33c2d234b1529bb876418ca  /tmp/types-node.tgz" | sha256sum -c - \
    && curl -fsSL -o /tmp/undici-types.tgz https://registry.npmjs.org/undici-types/-/undici-types-5.26.5.tgz \
    && echo "503bffdea1bee0526fb2ac75a4ac01ee5e712f251fabdeb8da4b408a519ed465  /tmp/undici-types.tgz" | sha256sum -c - \
    && mkdir -p /usr/local/lib/node_modules/@types/node /usr/local/lib/node_modules/undici-types \
    && tar -xzf /tmp/types-node.tgz -C /usr/local/lib/node_modules/@types/node --strip-components=1 \
    && tar -xzf /tmp/undici-types.tgz -C /usr/local/lib/node_modules/undici-types --strip-components=1 \
    && rm -f /tmp/types-node.tgz /tmp/undici-types.tgz \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Verifier tooling from a hash-pinned lockfile. Nothing is installed at trial time.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.txt && rm -f /tmp/requirements.txt

COPY --from=launcher /tool_launch /usr/local/bin/tool_launch
RUN useradd --create-home --uid 10001 --shell /bin/bash runner

# Landing directory for the declared artifact.
RUN mkdir -p /app/audit/src && chown -R runner:runner /app && chmod 1755 /app

# The weeks, their reports and the engine. Root only, never in a container the agent reaches.
COPY data /opt/truth
COPY golden /opt/truth/golden
COPY engine /opt/engine
COPY graded_tsconfig.json /opt/graded_tsconfig.json
RUN chmod 700 /opt/truth /opt/engine && chmod 644 /opt/graded_tsconfig.json

# Separate mode does not upload tests/, so the image owns /tests itself.
COPY . /tests/

WORKDIR /app/audit
