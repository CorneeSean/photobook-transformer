"""App Docker image entrypoint smoke test.

Runs the shipped app image with its **default ``ENTRYPOINT``** (no override) and
asserts the CLI actually launches. This guards the seam that unit tests can't
see: the ``[project.scripts]`` console-script name (installed into the image's
venv) and the ``Dockerfile`` ``ENTRYPOINT`` must name the *same* binary. When
they drift — e.g. the script was renamed to ``photobook-generator`` but the
``ENTRYPOINT`` still said ``album`` — ``docker compose up`` / ``docker run``
fails at container start with ``exec: "…": executable file not found in $PATH``
(OCI runtime error, exit 127), which no venv-based test catches because the venv
*does* have the script.

Marked ``integration`` (needs Docker + builds the app image), so ``poe
test-unit`` skips it; the session-scoped ``app_image`` build is shared with the
darktable render integration test (tests/conftest.py). ``build --help`` returns
during argparse — before any DB/Immich/env is touched — so the run is hermetic.
"""

from __future__ import annotations

import subprocess

import pytest

pytestmark = pytest.mark.integration


def test_default_entrypoint_launches_the_cli(app_image: str) -> None:
    # No --entrypoint override: this exercises the image's own ENTRYPOINT exactly
    # as `docker compose up`/`run app` would, appending `build --help` as args.
    result = subprocess.run(
        ["docker", "run", "--rm", app_image, "build", "--help"],
        capture_output=True,
        text=True,
    )

    # A missing entrypoint binary is exit 127 with an OCI "executable file not
    # found" error — the exact failure this test exists to catch.
    assert result.returncode == 0, (
        f"the image ENTRYPOINT did not launch the CLI (exit {result.returncode}) — "
        "the [project.scripts] console-script name and the Dockerfile ENTRYPOINT "
        f"have likely drifted apart.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # The real CLI ran: argparse prints the usage line for the `build` subcommand,
    # stamped with the entrypoint's prog name.
    assert "photobook-generator build" in result.stdout, result.stdout
