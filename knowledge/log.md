# Directory Update Log

## 2026-07-12

* **Creation**: Transformed `PROJECT_BRIEF.md` into this OKF v0.1 knowledge
  bundle — split into per-concept documents (overview, domain model,
  architecture, working agreement, data model, album definition, tech stack,
  folder structure, build order, open questions) and a `stages/` subdirectory
  with one document per pipeline stage.
* **Decision**: Adopted **basedpyright** as the static type checker (added to the
  tech stack, the commit gate, and build step 0), with **Astral `ty`** documented
  as the planned future migration once it reaches a stable release.
* **Decision**: Encoded the manual workflow as **poethepoet** tasks runnable via
  `uv run poe <task>`, and required a human-readable `README.md` plus an
  `AGENTS.md` at scaffold time (build step 0).
* **Decision**: Resolved the `enhance`/`caption` contradiction — a **single
  combined vision agent** (`agents/analysis.py`) produces tone deltas *and* the
  caption in one persisted, idempotent call; both stages consume its output, so a
  selected photo costs exactly one vision round-trip.
* **Decision**: Settled timezone handling — all datetimes are stored tz-aware in
  **UTC** (`DateTime(timezone=True)`); `album.yml` carries explicit offsets or an
  album-level `timezone`; `definition.py` and `sync` normalize to UTC. The
  remaining open question is narrowed to determining each photo's source zone
  from EXIF/Immich (Open Question #3).
* **Process**: Added the per-step [implementation workflow](/implementation-workflow.md)
  (branch per step → TDD → sub-agent review → user review → squash → fast-forward
  merge; never commit to `main`) and a living [progress tracker](/progress.md).
* **Decision**: Added local `pre-commit` hooks at scaffold — `no-commit-to-branch`
  to protect `main`, plus ruff and basedpyright — with the full pytest suite left
  to `uv run poe check` and CI.
* **Build step 0 done** (commit `11d290f`): scaffolded the `uv` project (Python
  3.14, `album` console entry point), ruff/basedpyright/pytest + poethepoet
  tasks in `pyproject.toml`, the minimal package skeleton (`db/`, `immich/`,
  `agents/`, `stages/` sub-packages and a `cli.py` stub — `config.py`,
  `definition.py`, `flow.py` deliberately deferred to steps 3 and 14),
  `.gitignore`, `.pre-commit-config.yaml` (installed), and `README.md`.
  Verified `uv run poe check` green and a direct commit to `main` is rejected
  by the `no-commit-to-branch` hook. Sub-agent review caught and fixed a
  `.gitignore` pattern that failed to exclude files nested under
  `data/albums/<slug>/` and a leftover `uv init` template stub in
  `src/album/__init__.py`.
