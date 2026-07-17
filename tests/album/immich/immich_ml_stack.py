"""Bring up a real, offline Immich stack for the ML integration test (step 24).

This is the harness behind :mod:`tests.album.immich.test_immich_ml_integration`.
The declarative stack lives in ``tests/fixtures/immich/docker-compose.integration.yml``
(server, the pre-baked machine-learning image, the vectorchord Postgres, and
valkey); here we only *drive* it — via testcontainers'
:class:`~testcontainers.compose.DockerCompose`, so lifecycle, port resolution, and
Ryuk-backed cleanup are handled by the library rather than hand-rolled subprocess
plumbing (the same testcontainers dependency the pgvector tests already use).

**One-time internet, then offline.** :func:`prepare_offline_stack` builds the ML
model image (via the ``docker`` CLI — docker-py's build breaks under this sandbox's
py3.14 gcloud cred-helper, see ``test_render_integration.py``) *and* pre-pulls the
other three stack images. After it has run once, ``compose up`` finds every image
present and pulls nothing, so a test run touches no network — and no ML *model* is
ever fetched at test time regardless, since they are baked into the image and it
runs with ``HF_HUB_OFFLINE=1``.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
from testcontainers.compose import DockerCompose

from album.immich.client import ImmichClient

# Keep in lockstep with the Dockerfile/compose defaults.
IMMICH_VERSION = "v3.0.3"
ML_IMAGE_TAG = f"immich-ml-baked:{IMMICH_VERSION}"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "immich"
_DOCKERFILE = _FIXTURE_DIR / "ml-baked.Dockerfile"
_COMPOSE_FILENAME = "docker-compose.integration.yml"
# The images `compose up` would otherwise pull from the internet at test time;
# pre-pulled by prepare_offline_stack so a warmed run stays offline.
_STACK_PULL_SERVICES = ["immich-server", "database", "redis"]

_ADMIN_EMAIL = "it-admin@example.com"
_ADMIN_PASSWORD = "integration-test-password"  # throwaway local stack, not a secret

# Generous because the very first inference cold-loads the ONNX models into the
# ML service (seconds) on top of the async job-queue latency.
_SERVER_READY_TIMEOUT_S = 240.0
_JOBS_IDLE_TIMEOUT_S = 300.0
# If no queue has gone active within this grace window the cascade is treated as
# already finished (nothing to wait out) rather than waited on forever.
_CASCADE_START_GRACE_S = 45.0
_POLL_INTERVAL_S = 3.0


def _compose_env() -> dict[str, str]:
    """Process env plus the version pin, inherited by the compose subprocess."""
    return os.environ | {"IMMICH_VERSION": IMMICH_VERSION}


def ml_image_exists() -> bool:
    """Whether the pre-baked ML image is present locally."""
    probe = subprocess.run(
        ["docker", "image", "inspect", ML_IMAGE_TAG],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def bake_ml_image(*, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Build the pre-baked ML image (the models-download half of the one-time setup)."""
    return subprocess.run(
        [
            "docker",
            "build",
            "-t",
            ML_IMAGE_TAG,
            "--build-arg",
            f"IMMICH_VERSION={IMMICH_VERSION}",
            "-f",
            str(_DOCKERFILE),
            str(_FIXTURE_DIR),
        ],
        capture_output=capture,
        text=True,
        env=_compose_env(),
    )


def prepare_offline_stack(*, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """The one-time, internet-touching setup: bake the ML image, then pre-pull the
    server/db/redis images so subsequent ``compose up`` runs pull nothing.

    Returns the first failing step's result (or the successful pull). The poe task
    ``bake-immich-ml`` calls this with ``capture=False`` to stream progress; the
    test fixture calls it (capturing) only when the ML image is missing.
    """
    build = bake_ml_image(capture=capture)
    if build.returncode != 0:
        return build
    return subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(_FIXTURE_DIR / _COMPOSE_FILENAME),
            "pull",
            *_STACK_PULL_SERVICES,
        ],
        capture_output=capture,
        text=True,
        env=_compose_env(),
    )


def _wait_for_server(server_base: str) -> None:
    """Poll ``/server/ping`` until Immich answers or the readiness budget elapses.

    Answering implies the whole chain is up: the server only serves once its DB
    migrations against the (dependency) Postgres have completed.
    """
    deadline = time.monotonic() + _SERVER_READY_TIMEOUT_S
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{server_base}/server/ping", timeout=5.0)
            if resp.status_code == 200:
                return
        except httpx.HTTPError as err:  # not up yet
            last_err = err
        time.sleep(_POLL_INTERVAL_S)
    raise RuntimeError(f"Immich server never became ready at {server_base}: {last_err}")


def _bootstrap_api_key(server_base: str) -> str:
    """Create the first admin, log in, and mint an API key — Immich's normal
    first-run bootstrap, done over its auth API so :class:`ImmichClient` has a key.

    The admin sign-up 400s if an admin already exists (a reused stack); that's
    fine — we only need the login + key afterwards.
    """
    httpx.post(
        f"{server_base}/auth/admin-sign-up",
        json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD, "name": "IT Admin"},
        timeout=15.0,
    )
    login = httpx.post(
        f"{server_base}/auth/login",
        json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD},
        timeout=15.0,
    )
    login.raise_for_status()
    token = login.json()["accessToken"]
    created = httpx.post(
        f"{server_base}/api-keys",
        json={"name": "integration-test", "permissions": ["all"]},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15.0,
    )
    created.raise_for_status()
    return created.json()["secret"]


def wait_jobs_idle(client: ImmichClient) -> None:
    """Block until Immich's post-upload job cascade has run and drained.

    An upload cascades through several queues (metadata → thumbnail → ML face
    detection + smart search). We first wait for *some* queue to go active (so we
    don't declare victory in the gap before the cascade is even enqueued), then
    wait for the whole set to read idle on several consecutive polls. If nothing
    goes active within the grace window, the cascade is treated as already done.
    """
    start = time.monotonic()
    deadline = start + _JOBS_IDLE_TIMEOUT_S
    seen_active = False
    consecutive_idle = 0
    while time.monotonic() < deadline:
        counts = client.get_jobs().values()
        idle = all(c.active == 0 and c.waiting == 0 for c in counts)
        if not idle:
            seen_active = True
            consecutive_idle = 0
        else:
            consecutive_idle += 1
            settled = seen_active or (time.monotonic() - start) >= _CASCADE_START_GRACE_S
            if settled and consecutive_idle >= 3:
                return
        time.sleep(_POLL_INTERVAL_S)
    raise RuntimeError(f"Immich job queues never drained within {_JOBS_IDLE_TIMEOUT_S:g}s")


@contextmanager
def immich_stack() -> Iterator[tuple[str, str]]:
    """Run the offline Immich stack for the duration of the context.

    Yields ``(api_base_url, api_key)`` — the ``…/api`` base and a freshly-minted
    key, ready to construct an :class:`ImmichClient`. testcontainers tears the
    stack down (and its volumes) on exit and Ryuk-reaps it even on a hard kill.
    """
    os.environ["IMMICH_VERSION"] = IMMICH_VERSION  # inherited by the compose subprocess
    # wait=False: `up -d` returns immediately and we gate on the server API
    # ourselves (bounded), rather than compose's unbounded `--wait`.
    compose = DockerCompose(
        context=str(_FIXTURE_DIR),
        compose_file_name=_COMPOSE_FILENAME,
        pull=False,  # images are pre-pulled by prepare_offline_stack — stay offline
        wait=False,
    )
    compose.start()
    try:
        host, port = compose.get_service_host_and_port("immich-server", 2283)
        server_base = f"http://{host}:{port}/api"
        _wait_for_server(server_base)
        api_key = _bootstrap_api_key(server_base)
        yield server_base, api_key
    finally:
        compose.stop()
