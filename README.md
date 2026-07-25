# photobook-transformer

A monorepo with two sibling projects:

| Dir | What it is | Stack |
| --- | --- | --- |
| **[`pipeline/`](./pipeline/)** | The batch pipeline that turns a raw photo dump into a curated, enhanced, captioned, print-ready book. The mature product. | Python 3.14, `uv`, Prefect, SQLAlchemy + Postgres/pgvector, open_clip |
| **[`rust-client/`](./rust-client/)** | A greenfield spike testing how much of the analysis/select stack can run **client-side in the browser**. First non-Python stack. | Svelte + shadcn-svelte, Rust → WASM (wgpu + sqlite-vec), transformers.js |

## Getting started

- **Pipeline** — run all commands from `pipeline/`:
  ```sh
  cd pipeline
  uv sync
  uv run poe check                       # full gate (ruff + basedpyright + xenon + pytest)
  uv run photobook-generator build <dir> # run the pipeline
  ```
  See [`pipeline/README.md`](./pipeline/README.md) and the
  [`knowledge/`](./knowledge/index.md) OKF bundle (the source of truth).

- **rust-client** — see [`rust-client/README.md`](./rust-client/README.md). Deliberately
  outside the Python gate. Design brief:
  [`knowledge/build/drafts/rust-client.md`](./knowledge/build/drafts/rust-client.md).

## Docs & conventions

[`AGENTS.md`](./AGENTS.md) is the operational summary for coding agents; the
[`knowledge/`](./knowledge/index.md) bundle is the detail and the single source of truth.
