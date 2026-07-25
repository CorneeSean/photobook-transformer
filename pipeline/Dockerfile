# syntax=docker/dockerfile:1

# --- builder: resolve deps + build the venv with uv -------------------------
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Install deps first so dependency layers cache independently of source changes.
# --extra scoring pulls in torch + open_clip for the real LAION/CLIP aesthetic
# scorer (CPU inference in the container; no GPU in Docker's Linux VM). Without it
# the flow silently degrades to the NULL-scoring placeholder.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev --extra scoring

COPY README.md ./
COPY src/ src/
RUN uv sync --locked --no-dev --extra scoring

# --- runtime: venv + darktable-cli (needed by stages/render.py) -------------
FROM python:3.14-slim AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends darktable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
# Jinja page/book templates are loaded from /app/templates at runtime
# (printers/html.py + flow/stages/assemble.py resolve it relative to the tree);
# they live outside src/, so copy them explicitly or assemble/fulfill can't render.
COPY templates/ templates/
COPY pyproject.toml uv.lock ./
ENV PATH="/app/.venv/bin:$PATH"

# Bake the aesthetic scorer weights (ViT-L/14 CLIP + LAION head, ~1 GB) into
# /app/.cache/aesthetic so `quality` runs fully offline — scorer defaults to
# "clip", so this is what makes real curation work in the image. Passing an
# explicit QualitySettings() avoids needing DATABASE_URL at build; the relative
# weights_dir resolves under WORKDIR /app both here and at runtime.
#
# The fetch imports `album.analysis` (its __init__ eagerly imports the captioner),
# so this layer's cache is busted by any src/ edit — but the ~1 GB download must
# not repeat every time. So download into a persistent BuildKit cache mount, then
# copy it into the image: on a re-run `fetch_weights` finds the weights already in
# the cache and downloads nothing (open_clip checks its cache_dir; the head is
# guarded by head_path.exists()), leaving only the fast local copy. Needs network
# on the first build only.
RUN --mount=type=cache,target=/opt/aesthetic-cache \
    python -c "from album.config import QualitySettings; from album.analysis.aesthetic import fetch_weights; fetch_weights(QualitySettings(weights_dir='/opt/aesthetic-cache'))" \
    && mkdir -p .cache/aesthetic \
    && cp -a /opt/aesthetic-cache/. .cache/aesthetic/

# Weights are baked above, so make huggingface_hub load them straight from the
# cache with no metadata ping — silences the "unauthenticated HF request" warning
# and drops the runtime dependency on HF Hub being reachable. Set AFTER the fetch
# RUN so the build-time download still has network.
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

ENTRYPOINT ["photobook-generator"]
CMD ["--help"]
