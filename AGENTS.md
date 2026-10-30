# Agent guidelines: trex-web-gui

These are the rules for an AI coding agent working in this repository. `CONTRIBUTING.md` is the full guide for
contributors; where the two differ, `CONTRIBUTING.md` wins.

## The project

trex-web-gui is a browser GUI for the TRex traffic generator in stateless (STL) mode: a Python backend (FastAPI, in
`backend/`) that drives a TRex server through a vendored copy of the TRex client, and a React and TypeScript
frontend (in `frontend/`). It is a single-user application that serves only on `127.0.0.1`. The features
`CONTRIBUTING.md` lists under "Declined by design" (more than one user, a `--listen` option, ASTF control) are
decisions, not gaps, and the loopback-only serving is deliberate (`SECURITY.md`, "What trex-web-gui is built for"):
do not "fix" them.

## Commands

- `npm --prefix frontend install` once after cloning, and again when `frontend/package.json` changes.
- `make check` is the gate: lint, the typecheck, the Python and frontend tests, the frontend build, the citation
  check and the docstring check. Its last line is `gate check rc=N in <time>`; a change is done when it reads
  `rc=0`. Without a systemd user session, run `make check PYTEST="uv run --project backend --locked pytest"`.
- One frontend test file: `npm --prefix frontend test -- --run src/<file>.test.ts`. Run vitest this way, through
  the frontend's npm script; another way of starting it can skip the test environment.
- One Python test file: `uv run --project backend pytest -q tests/unit/<file>.py`.
- `make api-docs` regenerates `docs/api.md` after a change to an HTTP route; do not edit `docs/api.md` by hand.

## Conventions

- Every function has a docstring or a real comment (`make docstrings` checks it).
- Every `file:line` citation in code, tests and docs points at the line it means (`make citations` checks it). A
  citation into a Markdown file carries a quoted snippet of the cited text, because lines move under a number.
  When you move lines, fix the citations that pointed at them.
- `backend/trex_web_gui/_vendor/` is generated. Change the vendored TRex client with a patch in
  `backend/vendor/patches/` and a row in `backend/vendor/patches.md`, never by editing the copy.
- A change in behaviour comes with a test that fails without it.

## Authorship

A human is the author: do not sign or attribute on their behalf. Do not add a `Signed-off-by` line, sign a commit
or tag, or state that the human reviewed or approved something. The human discloses AI use in the pull request,
as the "AI-assisted contributions" section of `CONTRIBUTING.md` asks, and answers for the change.
