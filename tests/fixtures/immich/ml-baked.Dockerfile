# Pre-baked Immich machine-learning image for the OFFLINE ML integration test
# (TestContainers playbook step 24). The stock `immich-machine-learning` image
# downloads its CLIP + face ONNX models from HuggingFace on first use; this bakes
# them into the image at BUILD time — the *only* step that touches the internet —
# so the integration test itself runs fully offline (HF_HUB_OFFLINE=1).
#
# The bake mirrors `immich_ml.models.base.ModelBase._download`: same repo id
# (`immich-app/<model>`), same on-disk layout
# (`settings.cache_folder / <task> / <model_name>`, with `cache_folder=/cache`),
# and the same armnn/rknn ignore. So the service finds the models where it looks:
#   /cache/clip/<model>/{visual,textual}/model.onnx
#   /cache/facial-recognition/<model>/{detection,recognition}/model.onnx
# Only the CPU `.onnx` variants are fetched (~0.8 GB); the ARM (`.armnn`) and
# Rockchip-NPU (`.rknn`) variants in those repos (~2.8 GB extra) are skipped.
#
# Build (one-time, needs internet):  uv run poe bake-immich-ml
# Keep IMMICH_VERSION in lockstep with the server image in
# docker-compose.integration.yml so the baked models match what the server asks
# the ML service to load (a fresh instance uses the default model names below).
ARG IMMICH_VERSION=v3.0.3
FROM ghcr.io/immich-app/immich-machine-learning:${IMMICH_VERSION}

# Immich's default model names (a fresh instance requests exactly these). Keep in
# sync with the compose; override at build time if the server defaults change.
ARG CLIP_MODEL=ViT-B-32__openai
ARG FACE_MODEL=buffalo_l

# `huggingface_hub` ships inside the ML image already. `local_dir` writes the real
# model files flat into <dest> (where the service's ModelBase._download puts them),
# but ALSO keeps a duplicate blob copy under <dest>/.cache — which we delete in the
# SAME RUN layer, since a later RUN could only whiteout, not shrink, a committed
# layer. The flat model.onnx files are what the service reads.
RUN python3 - "$CLIP_MODEL" "$FACE_MODEL" <<'PY'
import shutil
import sys

from huggingface_hub import snapshot_download

clip_model, face_model = sys.argv[1], sys.argv[2]
cache = "/cache"
# (task dir, model name) — the task dirs mirror immich_ml's ModelTask.value.
for task, name in (("clip", clip_model), ("facial-recognition", face_model)):
    dest = f"{cache}/{task}/{name}"
    snapshot_download(
        f"immich-app/{name}",
        local_dir=dest,
        ignore_patterns=["*.armnn", "*.rknn"],  # CPU ONNX only
    )
    shutil.rmtree(f"{dest}/.cache", ignore_errors=True)  # drop the duplicate blobs
    print(f"baked {task}/{name} -> {dest}", flush=True)
PY

# Belt-and-suspenders: at runtime a cache miss must fail loudly, never silently
# reach the internet — a hermetic offline test is the whole point of this image.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
