# syntax=docker/dockerfile:1

# --- builder: resolve deps + build the venv with uv -------------------------
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Install deps first so dependency layers cache independently of source changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY README.md ./
COPY src/ src/
RUN uv sync --locked --no-dev

# --- runtime: venv + darktable-cli (needed by stages/render.py) -------------
FROM python:3.14-slim AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends darktable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY pyproject.toml uv.lock ./
ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["album"]
CMD ["--help"]
