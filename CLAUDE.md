# CLAUDE.md

See [AGENTS.md](./AGENTS.md) for the full project guidance. It is the single source of truth for build/test commands, architecture, and conventions in this repository.

This is a monorepo: the Python pipeline lives in [`pipeline/`](./pipeline/) (run all its
commands from there, e.g. `cd pipeline && uv run poe check`) and the client-side WASM
spike in [`rust-client/`](./rust-client/). See AGENTS.md's "Repository layout" section.
