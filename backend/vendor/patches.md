# Vendor patches

trex-web-gui carries its own copy of the TRex Python client in `backend/trex_web_gui/_vendor/`, with thirteen patches
applied (`backend/vendor/patches/`). This file records what each patch fixes, who wrote it, how the copy is
built and checked, and where each fix stands upstream. Researched 2026-10-18.

## The pin

- Upstream: `cisco-system-traffic-generator/trex-core`, commit `27e0153b5` (2025-11-05, "v3.0.8" merge).
  That is upstream `master`'s HEAD: the repository has had no push since 2025-11-05, so every bug below is
  still present on upstream `master`. Re-check with
  `gh api repos/cisco-system-traffic-generator/trex-core --jq .pushed_at`.
- Source: a trex-core checkout at `TREX_PIN` (`TREX_SRC`; no git history; `VERSION` reads
  `v3.08`). `backend/vendor/TREX_PIN` records the SHA, a sha256 over the copied source tree, and the patch
  list.

## How the patches are used

They are applied once, at build time; nothing applies them at runtime.

1. `make vendor` runs `backend/scripts/vendor_trex.py`. It copies the client and its bundled libraries from
   the clone, applies every patch in number order, and writes `backend/trex_web_gui/_vendor/` plus
   `MANIFEST.sha256` and `TREX_PIN`. An unchanged clone always produces byte-identical output.
2. The patched copy is committed. The GUI imports `trex` only from `_vendor/`, never from a system install.
3. `make check` runs `tests/unit/test_vendor_patches.py` when the vendor inputs (`VENDOR_PATHS` in the
   `Makefile`) differ from their merge base with `origin/main`, working tree included, and skips it otherwise;
   `make check VENDOR_TESTS=run` forces it, and the release gates always pass that. It rebuilds the copy and
   requires it to match the committed one byte for byte. For each patch it also rebuilds *without* that patch
   (`--skip-patch NN`) and requires the bug to come back, so each patch is shown to be both needed and working.

Rules:

- Never edit `_vendor/` by hand; the reproducibility test fails on any change that is not a patch.
- **Adding a patch:**
  1. Write `NN-short-name.patch` with `diff -ruN a/… b/…` hunks.
  2. Start it with a header saying what it fixes, where the bug came from, and its upstream status.
  3. Add it to the `patches` list in `backend/vendor/TREX_PIN`.
  4. Run `make vendor`.
  5. Add a `test_NN` that fails when the patch is skipped.

  Patch 12 is the latest example.
- **Re-pinning to a newer TRex:** run `make vendor` against the new checkout. A patch that no longer applies,
  or whose bug upstream has fixed, fails its test; drop or rewrite it.

## Authorship

All thirteen patches were written by Claude (Claude Code implementer subagents running the trex-web-gui plans). Each
was reviewed by a separate reviewer agent, then committed under Charles Menser's git identity with a
`Co-Authored-By: Claude Opus 5.5` trailer. None came from upstream, and none has been submitted there.

| Patches | Commit | Date | Plan task |
|---|---|---|---|
| 01–07 | `a09b749` | 2026-10-16 | P1 Task 2 |
| 08 | `956a441` (header provenance in `d2af2e2`) | 2026-10-16 | P2 Task 2 |
| 09 | `b27b909`, reformatted in `1ca5a35` | 2026-10-17/18 | P3b Task 3 |
| 10 | branch `license-openssl` | 2026-10-18 | P3b follow-up 1 (licence) |
| 11 | branch `p4b-packet-editor` | 2026-10-25 | P4b Task 1 (packet editor) |
| 12 | branch `p4b-packet-editor` | 2026-10-25 | P4b final-gate fix (packet editor) |
| 13 | `main` | 2026-10-28 | P12 Task 2 (public split) |

## The patches

Paths are relative to `trex/` in the vendored copy. Upstream, that directory is
`scripts/automation/trex_control_plane/interactive/trex/`. Line numbers are upstream's at the pin.

| # | File | Bug | Effect without the patch | Upstream report? |
|---|---|---|---|---|
| 01 `load-system-libs` | `__init__.py` | trex-web-gui need, not a bug: the client always puts its bundled `external_libs` first on `sys.path`. | `TREX_SYSTEM_LIBS` is ignored, so the client cannot take `yaml` and `zmq` from the environment instead of `external_libs`. Since the bundled `pyyaml-3.11` and `pyzmq-ctypes` are no longer vendored (P6), `import trex` then fails to find `yaml`. | No: specific to embedding |
| 02 `yaml-safe-load` | `stl/trex_stl_streams.py:1105` | `yaml.load(f)` with no Loader. | With PyYAML ≥ 6, `STLProfile.load_yaml` raises "missing 1 required positional argument: 'Loader'". With a PyYAML before 5.1 (whose default loader is unsafe) a YAML profile can construct arbitrary Python objects. | Yes, draft E |
| 03 `scapy-themes-html-escape` | bundled `scapy-2.4.3/scapy/themes.py:14,366` | `import cgi` / `cgi.escape`. The `cgi` module was removed in Python 3.13 (PEP 594). | `import scapy.themes` fails on Python ≥ 3.13 with "No module named 'cgi'". Current Scapy already uses `html.escape`, so the real fix is upgrading the bundled Scapy. | Yes, draft E |
| 04 `raw-string-regexes` | `common/trex_subscriber.py`, `scapy_server/scapy_service.py`, `utils/common.py`, `utils/parsing_opts.py` | Regexes in plain strings, e.g. `'(\d+)'`. | 8 `SyntaxWarning: invalid escape sequence` on compile (Python ≥ 3.12); a future Python makes them errors. Open PR #1196 fixes only `utils/common.py` of these. | Yes, draft E |
| 05 `never-signal-host-process` | `common/trex_client.py:238`, `common/trex_conn.py:119` | The library signals its own process: SIGTERM when the event subscriber crashes, SIGINT on connection loss while an API call runs. | In any embedding application (a web server here), a subscriber crash kills the whole process and a lost connection raises `KeyboardInterrupt` in the main thread. | Yes, draft D (enhancement) |
| 06 `latency-stats-handler-name` | `common/trex_subscriber.py:401,405` | Two methods both named `handle_flow_stats_update`; the second (which emits "latency stats update") replaces the first. | `handle_latency_stats_update`, called at :381, does not exist (AttributeError), and a `flow_stats` publish would be reported as "latency stats update". **Dormant on v3:** the server publishes only global stats on that channel (`main_dpdk.cpp`, `publish_async_data`), so neither branch runs today. | Yes, draft C (low priority) |
| 07 `scapy-service-assert-message` | `scapy_server/scapy_service.py:893,895` | `e.message`; Python 3 exceptions have no `.message`. | A field-engine error in the Scapy service raises AttributeError instead of returning the error text. Not touched by open PR #1220. | Yes, draft E |
| 08 `set-attr-kwarg-names` | `common/trex_port.py:500-507` | `Port.set_attr` reads kwargs `link_status`, `led_status` and `flow_ctrl_mode`; every caller (`trex_client.py:2153-2204`, `reset(restart=True)`, console `portattr`) passes `link_up`, `led_on` and `flow_ctrl`. | Link up/down, LED and flow-control requests are silently dropped: the RPC carries no such attribute and the call returns success. Introduced by `39a62671c` (2018-05-24, v2.42). | Yes, draft A |
| 09 `icmp-dest-unreachable-name` | `common/services/trex_service_icmp.py:163` | Assigns `self.ICMP_TYPE_DEST_UNREACBLE`, a misspelling of the class constant at :138. | A destination-unreachable reply that reaches the record raises AttributeError instead of being recorded as state 3. Rare: the filter matches on ICMP id, and Scapy 2.4.3 leaves the id absent for type 3. | Yes, draft B |
| 10 `drop-trex-openssl` | `__init__.py:36` | trex-web-gui need, not a bug: `ext_libs` lists `trex_openssl`, whose bundled OpenSSL 1.1 `.so` files carry the OpenSSL/SSLeay licence, incompatible with the GPLv2 that covers the client and Scapy. `vendor_trex.py` no longer copies `external_libs/trex-openssl`. | Without the entry removed, `import trex` raises "Unable to find required external library: 'trex_openssl'" once the directory is gone. With it, only the WLC/AP modules (`stl/trex_stl_wlc.py:15`, `common/services/trex_service_ap.py:10`), which trex-web-gui does not use, fail at import. | No: specific to trex-web-gui's licence |
| 11 `enum-values-dict-paren` | `scapy_server/scapy_service.py:1119` | `len(field_data["values_dict"] > 0)` compares a dict with 0 (TypeError), and the bare `except:` around it hides the error. The parenthesis belongs after `["values_dict"]`. | Every `EnumField` (`Dot1Q.type`, `ARP.op`, ...) reports no `type` in `get_definitions`, so a client cannot tell it is an enumeration. | No: not yet reported. Open PR #1220 (bytes in templates) is a different bug in the same file; P4b vendors no patch for it (spec §7 Deviation 1). |
| 12 `flow-var-init-value` | `stl/trex_stl_packet_builder_scapy.py:1827-1835` | `STLPktBuilder.from_json` rebuilds each `flow_var` without its `init_value`, although `to_json` writes it. | Every JSON profile path (`STLProfile.load_json`, `from_json`, the packet editor's Copy as Python) drops an `init_value` that differs from the default (`min_value`, or `max_value` for `dec`): TRex counts from the default, and nothing says so. With the patch, a hand-written JSON profile whose `init_value` lies outside [`min_value`, `max_value`] fails to load, as TRex already does for a `.py` profile (P4b ledger :231). | No: not yet reported |
| 13 `py314-finally-flow` | `scapy_server/scapy_zmq_server.py:76-100,173-174`, `common/services/trex_service_ap.py:229-241` | A `return` or `break` inside `finally` (three sites), which Python 3.14 compiles with a SyntaxWarning (PEP 765). | On 3.14 each import of these modules prints the warnings; trex-web-gui never imports either module (the packet worker imports `scapy_service`), so only a compile of the tree shows them. With the patch the flow is the same for every exception the handlers catch: `error_handler` still returns its response when a handler itself raises, the server loop still ends after `shut_down`, and the AP cleanup still runs only with a capture id. What used to be swallowed now propagates: a `BaseException` such as `KeyboardInterrupt`, an exception from the reply's send during `shut_down`, and one raised inside the AP loop's own `except` handler while it has no capture id. | No: not yet reported |

PR #1220 (`get_template` returns bytes): handled in the caller, not patched. The packet editor decodes the
bytes where it calls `get_template` (spec §7 Deviation 1: no patch for it).

## Submitting upstream: the rules

Sources: upstream `CONTRIBUTING.md` (identical in the local clone and on `master`), the project wiki's "How
to contribute" section, the `signedoff/` directory, and the license files.

**Where:**

- Bugs and feature requests go to GitHub issues (the preferred tracker). The YouTrack tracker is legacy.
- Questions go to the Google group trex-tgn (trex-tgn@googlegroups.com). GitHub Discussions are also
  enabled.
- The repository has no issue or PR templates and no `SECURITY.md`.

**Pull requests:**

- Fork, use one narrowly focused branch, squash commits, open a PR. The wiki says a small fix can go
  straight to a PR.
- New code should come with unit tests showing the defect and its fix. Follow the existing style.
- Every commit must be signed off with `git commit -s` (`Signed-off-by: Full Name <e-mail>`), which
  certifies the Developer Certificate of Origin (DCO 1.1). `signedoff/` holds retroactive DCO sign-offs from
  past contributors. There is no CLA.
- The wiki's per-commit gate also asks for functional tests, regression on a bare-metal XL710 setup, and
  documentation. That is aimed at features; an outside contributor cannot run the XL710 regression.

**License:**

- `CONTRIBUTING.md` says every contribution, including filing a bug or issue, is released under Apache 2.0,
  and that submitting means agreeing to its "waiver of copyright interest".
- `LICENSE` (Apache 2.0) covers the server.
- `LICENSE_Python_Client` and `LICENSE_Scapy_Server` say the Python client and Scapy server are GPLv2,
  because they use Scapy.
- The patched files carry no per-file license header. A one-line fix to a GPL/Apache-licensed file is
  compatible with either reading.

**AI-assisted contributions:**

- Upstream states no policy.
- The nearest established norm is the Linux kernel's "AI Coding Assistants" document
  (docs.kernel.org/process/coding-assistants.html):
  - an AI agent must not add `Signed-off-by`, because only a human can certify the DCO;
  - the human submitter reviews the code, checks licensing, signs off, and takes responsibility;
  - AI help is disclosed with an `Assisted-by:` tag (in 7.x, `Assisted-by: AGENT_NAME:MODEL_VERSION`).
- Applied here: Charles reviews each draft and patch and signs off as the human contributor. The commit
  carries `Assisted-by: Claude:claude-opus-5-5`, and the issue text says the analysis and patch were
  AI-assisted and human-reviewed.
- Claude never signs off and never posts. Submitting is Charles's action.

**Upstream activity (2026-10-18):**

- Last push to `master`: 2025-11-05.
- Last GitHub PR merged: #1053, 2023-08-20. Maintainers commit directly.
- Outside PRs since 2025-09, such as #1196–#1200 and #1220, are all open with no maintainer reply.
- Recent issues, such as #1214 and #1221, show no maintainer comments.
- So a report is useful mainly as a public record and to other users; a fix may never be merged. Issues
  come first, with the fix as a diff in the issue. A PR is worth opening only for 08 and 09, the two clear
  one-line-class fixes.

**Existing reports (searched 2026-10-18):**

- No issue or PR covers 05, 06, 07, 08 or 09.
- PR #1196 (open) partly overlaps 04.
- Issue #1219 and PR #1220 concern a different `scapy_service.py` bug, bytes in templates.

**Privacy:**

- The drafts contain only upstream file paths, the public pin and code. Their repro steps use placeholders
  such as `<dst>`, never addresses from a real setup.
- Run the privacy scan before any commit of these files.

## Drafts

In `backend/vendor/upstream-drafts/`, ready for review. **Nothing has been submitted.**

| Draft | Covers | Kind | Priority |
|---|---|---|---|
| `A-set-attr-kwargs.md` | 08 | bug, plus a PR-ready diff | high: silent wrong behaviour |
| `B-icmp-unreachable-typo.md` | 09 | bug, plus a PR-ready diff | medium |
| `C-subscriber-duplicate-handler.md` | 06 | bug | low: dormant on v3 |
| `D-library-signals-host-process.md` | 05 | enhancement | medium for embedders |
| `E-python-3.12-3.13-compat.md` | 02, 03, 04, 07 | bug (one issue, four items) | medium |

Patches 01 and 10 are not reported: they adapt the client to trex-web-gui's packaging and licence and fix
nothing upstream.

## Open questions

- *Closed 2026-10-18:* **trex-web-gui's own license.** GPL-2.0-or-later, for the whole repository
  (`LICENSE`, `NOTICE`, `backend/pyproject.toml`, `frontend/package.json`). It is compatible with the
  vendored client (GPLv2 or later, `LICENSE_Python_Client`) and Scapy 2.4.3 (GPLv2); the GPL-incompatible
  `trex-openssl` is no longer shipped (patch 10).
