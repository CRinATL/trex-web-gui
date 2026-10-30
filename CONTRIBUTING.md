# Contributing to trex-web-gui

Issues and pull requests are welcome. trex-web-gui has one maintainer, who answers on a best-effort basis; there is no
response-time promise. Questions about TRex itself belong upstream, at
[trex-core](https://github.com/cisco-system-traffic-generator/trex-core). Report a security problem as
[`SECURITY.md`](SECURITY.md) says, not in a public issue.

## Prerequisites

- **Linux with a systemd user session.** The Makefile runs the Python tests under `systemd-run --user` with a memory
  cap, so a test that allocates without bound is killed on its own instead of exhausting the machine. Without a user
  session (a container, some CI runners), run the tests with the cap removed:
  `make test PYTEST="uv run --project backend pytest"`. `make check-py`, which runs the unit tests on Python 3.12,
  3.13 and 3.14, has no such override and needs the session.
- **[uv](https://docs.astral.sh/uv/).** It provides Python 3.12 or later and the backend's dependencies; nothing is
  installed system-wide.
- **Node.js with npm**, a version `frontend/package.json` accepts (`engines`: `^22.22.2 || ^24.15.0 || >=26.0.0`,
  the range jsdom, a test dependency, requires). npm only warns on another version.
- **A TRex v3.08 server** in stateless mode, to use the GUI against. The tests do not need one: they run against a
  fake TRex built from recordings.

## Getting started

```
npm --prefix frontend install        # once after cloning, and again when frontend/package.json changes
make check                           # lint, typecheck, Python and frontend tests, build, citations, docstrings
```

To work on the GUI, run two processes, the backend and the Vite dev server:

```
make dev                             # the backend on 127.0.0.1:8765, API only
npm --prefix frontend run dev        # the UI on :5173, reloading on every edit
```

Then open the `:5173` login URL that `make dev` prints. To run the GUI as a user would, `make build` once, then
`uv run --project backend trex-web-gui serve`.

## Commands

| Command | What it does |
|---|---|
| `npm --prefix frontend install` | Installs the UI's npm dependencies into `frontend/node_modules`. Run it once after cloning, and again when `frontend/package.json` changes, before `make build` or `make check` |
| `make build` | Builds the UI that `trex-web-gui serve` serves |
| `make wheel` | Builds the one wheel in `dist/`, with the UI built first and carried inside it |
| `make install-smoke` | Installs the wheel from `dist/` into a throwaway `uv tool` directory on Python 3.12 and 3.14 and exercises it |
| `make check` | Runs lint, the typecheck, the unit and contract tests (against a fake TRex, including the check that `docs/api.md` is current) and the frontend tests, builds the frontend, then checks the `file:line` citations and the docstrings. Each step prints its usual time and when the gate should end, and a table at the end compares them; the last line is `gate check rc=N in <time>`. pytest runs on every core; `PYTEST_WORKERS=0` runs it in one process |
| `make test-py` / `make test-js` | The backend tests alone, or the frontend tests alone (`make test` runs both) |
| `make check-py` | Runs the unit tests on Python 3.12, 3.13 and 3.14, one step each |
| `python3 -I scripts/gate.py status` | Where a running `make check`, `check-py` or `install-smoke` is: the step, how long it has run against its usual time, and when it should end. `gate.py times` lists each step's usual time |
| `make check-golden` | Checks every stock stateless profile against the snapshots in `tests/golden/stl` (`WRITE_GOLDEN=1` rewrites them) |
| `make dev` | Starts the backend on `127.0.0.1:8765` without the UI (`trex-web-gui serve --dev`). Run `npm --prefix frontend run dev` to serve the UI on `:5173`, then open the `:5173` login URL the backend prints |
| `make citations` | Checks every `file:line` citation in code, tests and docs |
| `make docstrings` | Checks that every project function has a docstring or a real comment |
| `make api-docs` | Regenerates [`docs/api.md`](docs/api.md), the HTTP API reference, from the routes' docstrings and models |
| `make wire-fixtures` | Re-records the JSON files in `frontend/src/__fixtures__/` from the stateful fake |

### Maintainer-only targets

These need things a clone does not have. They are listed so that their names in the Makefile make sense.

| Command | What it needs |
|---|---|
| `make vendor` | A trex-core checkout at the commit in `backend/vendor/TREX_PIN` (`TREX_SRC`). It rebuilds `backend/trex_web_gui/_vendor/` and applies `backend/vendor/patches/`; the result is committed, so nothing else needs the checkout |
| `make check-real`, `make check-real-all`, `make check-daemon-real` | The KVM test VM ([`vm/README.md`](vm/README.md)), which runs a real TRex. The `record-*` targets re-record the test fixtures against it |
| `make deferred`, `make release-rc`, `make release` | The maintainer's private development records: the register of deferred items, and the use logs the release gate reads |
| `make parity`, `make parity-sync` | A private planning inventory (`TREX_PLANS`) that the v1.0 parity checklist is generated from |

Three Makefile variables name machine-specific inputs, overridable on the command line or in the environment:
`TREX_SRC` (the trex-core checkout), `TREX_PLANS` (the planning inventory) and `TREX_VM_GUEST` (which test guest the
VM runs, `jammy` by default; see [`vm/README.md`](vm/README.md)).

## Conventions

- Every function has a docstring or a real comment (`make docstrings`), and every `file:line` citation in code,
  tests and docs must point at the line it means (`make citations`). A citation into a Markdown file carries a
  quoted snippet, because lines move under a number.
- Code and comments carry tags such as `canon:TSK-5`, phase and task labels (`P4a Task 1`) and decision numbers
  (`D10`). They name entries in the maintainer's private development records and are kept as provenance; a change
  does not need to add them.
- `backend/trex_web_gui/_vendor/` is generated. Change the vendored TRex client with a patch in
  `backend/vendor/patches/` and a row in `backend/vendor/patches.md`, not by editing the copy.

## Before a large change

Open an issue first and describe what you want to change and why. A large pull request that arrives without one,
for work nobody has discussed, may be closed so that neither of us spends time on a change that will not be merged.
A small fix (a typo, a broken link, an obvious bug with its test) can come straight as a pull request.

## Declined by design

These are decided, not missing. A pull request that adds one is closed:

- **More than one user.** trex-web-gui is a single-user application; every logged-in browser tab shares its one TRex
  session.
- **A `--listen` option**, or any way to serve on an address other than `127.0.0.1`. A browser on another machine
  uses an SSH tunnel ([`SECURITY.md`](SECURITY.md) says why).
- **ASTF control.** Traffic control targets TRex in stateless (STL) mode only.

## Pull requests

- `make check` passes. Without a systemd user session, run it as the CI workflow does:
  `make check PYTEST="uv run --project backend --locked pytest"`.
- A change in behaviour comes with a test that fails without it.
- When a change adds or changes an HTTP route, run `make api-docs` and commit the regenerated
  [`docs/api.md`](docs/api.md).
- Keep a pull request to one change, and say in its description what it changes and why.

## AI-assisted contributions

trex-web-gui itself is developed with AI coding agents, and contributions made with them are welcome on these terms:

- **Disclose substantial AI use** in the pull request description: which tool, and how much of the change it
  wrote. A commit trailer naming the tool is welcome but not required.
- **You can explain the change in your own words.** Read and understand all of it before you open the pull request,
  and be ready to answer questions about any line. A pull request whose author cannot do that is closed.
- **You are the author**, and you are accountable for what you submit, whatever wrote it. You must have the right
  to submit it under the project's licence; AI output that reproduces third-party code keeps that code's licence.
- **Verify an AI-written issue or security report yourself** before you send it: reproduce the problem and check
  each claim. An unverified one is closed without analysis.
- **No unattended agents.** Pull requests, issues and review comments come from a person who has read them, not
  from an agent acting on its own; a pull request from a bot account is closed.

## Licence

trex-web-gui is GPL-2.0-or-later ([`LICENSE`](LICENSE), [`NOTICE`](NOTICE)). A contribution is accepted under the same
licence. There is no contributor licence agreement and no sign-off: by opening a pull request you agree that your
contribution is under GPL-2.0-or-later.
