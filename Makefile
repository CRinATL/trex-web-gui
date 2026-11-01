UV      := uv run --project backend
# Machine-specific paths; override on the command line or in the environment.
# TREX_SRC: the pinned trex-core clone (make vendor, vendor tests). TREX_PLANS: trex-rework's plans directory
# (make parity reads its inventory-merged.md, never writes). TREX_VM_GUEST: the test guest (vm/guests.sh
# derives its work dir and ports; empty means jammy). No trailing comments here: make would keep the spaces
# before them in the value. The defaults are the maintainer's layout; override them for yours.
TREX_SRC    ?= $(HOME)/Projects/trex-rework/trex-core
TREX_PLANS  ?= $(HOME)/Projects/trex-rework/plans
export TREX_SRC TREX_VM_GUEST
# The vendor-rebuild tests (marker vendor_build) run only when the vendor inputs differ from VENDOR_BASE
# (default: the merge base with origin/main, so unpushed commits on main count), working tree included.
# The lock file and pyproject count too: test_01 depends on the venv's pyzmq. A final gate passes
# VENDOR_TESTS=run.
VENDOR_PATHS := backend/vendor backend/scripts/vendor_trex.py backend/trex_web_gui/_vendor backend/uv.lock \
                backend/pyproject.toml
VENDOR_BASE  ?= $(shell git merge-base HEAD origin/main 2>/dev/null || git merge-base HEAD main 2>/dev/null || echo HEAD)
VENDOR_TESTS ?= $(shell git diff --quiet $(VENDOR_BASE) -- $(VENDOR_PATHS) 2>/dev/null \
                  && [ -z "$$(git ls-files --others --exclude-standard -- $(VENDOR_PATHS))" ] && echo skip || echo run)
# make test-py and check-py run pytest on every core (pytest-xdist), hence the 8G cap; the usual times are in
# `python3 -I scripts/gate.py times`. PYTEST_WORKERS=0 runs one process, for chasing a test that only fails under
# load. Targets that can rewrite files in the repo (golden snapshots, wire fixtures) stay in one process.
PYTEST_WORKERS ?= auto
# make test-py schedules with worksteal: `load` sends consecutive tests to one worker in chunks, so a file of
# slow tests becomes one serial chain. PYTEST_DIST=load restores the old scheduling.
PYTEST_DIST ?= worksteal
# vmThreads keeps every vitest worker in one process, so test-js gets the same cap as pytest; CI empties it.
VITEST_CAP ?= systemd-run --user --scope -q -p MemoryMax=8G -p MemorySwapMax=0 --
PYTEST  := systemd-run --user --scope -q -p MemoryMax=8G -p MemorySwapMax=0 -- $(UV) pytest
.PHONY: FORCE check check-golden lint typecheck test test-py test-js build check-py check-real check-real-all check-daemon-real record-p2 record-p3 record-p3a record-p3b record-p3b-v6 record-p3c record-p5 record-p5-stf record-netem vendor dev wire-fixtures citations deferred docstrings api-docs wheel install-smoke install-smoke-run release-rc release release-rc-publish release-build release-publish parity parity-sync

# The gates below run their steps one at a time through scripts/gate.py: each step's usual time, the expected end,
# a status file (`python3 -I scripts/gate.py status`) and a timing history in .superpowers/gate/.
check:            # lint typecheck test-py test-js build citations docstrings
	@python3 -I scripts/gate.py run check

lint:
	$(UV) ruff check backend tests scripts vm
	@if [ -f frontend/package.json ]; then npm --prefix frontend run lint; fi

typecheck:
	cd backend && uv run mypy trex_web_gui
	@if [ -f frontend/package.json ]; then npm --prefix frontend run typecheck; fi

test: test-py test-js

test-py:
	@[ "$(VENDOR_TESTS)" = run ] || echo "vendor rebuild tests skipped: no change under the vendor paths since $(VENDOR_BASE) (VENDOR_TESTS=run forces them)"
	$(PYTEST) -q -n $(PYTEST_WORKERS) --dist $(PYTEST_DIST) $(if $(filter run,$(VENDOR_TESTS)),,-m "not real and not vendor_build and not golden")

test-js:
	@if [ -f frontend/package.json ]; then $(VITEST_CAP) npm --prefix frontend test -- --run; fi

build:
	@if [ -f frontend/package.json ]; then npm --prefix frontend run build; fi
	@if [ -f frontend/package.json ]; then grep -q trex-web-gui.theme frontend/dist/index.html || { echo "frontend/dist/index.html lost the theme pre-paint script (trex-web-gui.theme)"; exit 1; }; fi

wheel:         # the one wheel in dist/: a stale trex_web_gui-*.whl is deleted first (D14)
	@if [ -d dist ]; then find dist -maxdepth 1 -name 'trex_web_gui-*.whl' -delete; fi
	$(MAKE) build
	systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 -- uv build --project backend --wheel --out-dir dist

SMOKE_PYTHONS ?= 3.12 3.14

install-smoke:   # P6: the wheel in dist/ installed into a throwaway uv tool dir on each Python and exercised
	@python3 -I scripts/gate.py run install-smoke

install-smoke-run:
	systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 -- $(UV) python -I -B scripts/install_smoke.py run --pythons "$(SMOKE_PYTHONS)"

release-rc:      # historical (the private candidates / v1.0.0's F1): P6 gate 1, for the candidate backend/pyproject.toml names (1.0.0rcN): preconditions, the gate chain, then the tag/push/gh commands PRINTED (DRY_RUN=1 allows a branch other than main)
	$(UV) python -I -B scripts/release.py check rc $(if $(DRY_RUN),--dry-run,)

release:         # a 1.0.x patch (version from backend/pyproject.toml): the gate chain incl. check-real, every host finding closed, the records pushed; prints the publish step
	$(UV) python -I -B scripts/release.py check patch $(if $(DRY_RUN),--dry-run,)

release-rc-publish:  # historical (the private candidates / v1.0.0's F1): after a passing release-rc: push main if needed, tag, push the tag, create the GitHub release, check the uploaded wheel's sha256; resumes after a failure (run by the person, canon:WRK-3)
	$(UV) python -I -B scripts/release.py publish rc

release-build:       # historical (the private candidates / v1.0.0's F1): F1, in the public clone (no records, so no gate 2 there): make wheel and install-smoke, then the record release-publish reads
	$(UV) python -I -B scripts/release.py build final

release-publish:     # after a passing make release: signed tag, the release as a draft, the draft's wheel checked; never pushes main; PUBLISH_DRAFT=1 publishes the reviewed draft (run by the person, canon:WRK-3)
	$(UV) python -I -B scripts/release.py publish patch $(if $(PUBLISH_DRAFT),--publish-draft,)

check-py:         # tests/unit on 3.12, 3.13 and 3.14, one step each
	@python3 -I scripts/gate.py run check-py

check-py-%: FORCE  # vendor rebuilds run once, in make test; 3.13 is covered by test_03
	systemd-run --user --scope -q -p MemoryMax=8G -p MemorySwapMax=0 -- \
	  uv run --project backend --python $* --isolated pytest -q -n $(PYTEST_WORKERS) tests/unit \
	  --deselect tests/unit/test_vendor_patches.py

FORCE:            # a pattern rule cannot be .PHONY: this makes a file named check-py-3.12 never count as done

check-golden:    # P4a: every stock stl profile against tests/golden/stl (WRITE_GOLDEN=1 rewrites the snapshots)
	$(PYTEST) -q -m golden tests/golden

check-real:
	vm/real.sh

check-real-all:
	vm/real_all.sh

record-p2:
	TREX_VM_RECORD=p2 vm/real.sh

record-p3:
	TREX_VM_RECORD=p3 vm/real.sh

record-p3a:
	TREX_VM_RECORD=p3a vm/real.sh

record-p3b:
	TREX_VM_RECORD=p3b vm/real.sh

record-p3b-v6:    # the linux_based stack (P3b follow-up 2): vm/real.sh switches the guest and back
	TREX_VM_RECORD=p3b-v6 vm/real.sh

record-p3c:    # P3c: the capture RPC family, packets and Scapy trees
	TREX_VM_RECORD=p3c vm/real.sh

record-p5:    # P5: the monitor and daemon fixtures; real.sh stops the daemon and restores stateless afterwards
	TREX_VM_RECORD=p5 vm/real.sh

record-p5-stf:    # P5: a daemon-started STF run, the file listings and the upload gate; real.sh stops the daemon and restores stateless
	TREX_VM_RECORD=p5-stf vm/real.sh

check-daemon-real:    # P5: the real tests that restart the guest's TRex (marker restarts_trex); jammy only
	TREX_VM_MARK=restarts_trex vm/real.sh

record-netem:    # P3b follow-up 4: needs TREX_NETEM_BEFORE, a 4de803a worktree (git worktree add --detach <dir> 4de803a)
	test -d "$$TREX_NETEM_BEFORE" || { echo "TREX_NETEM_BEFORE must name a 4de803a worktree"; exit 2; }
	TREX_VM_RECORD=netem vm/real.sh

vendor:
	$(UV) python -I -B backend/scripts/vendor_trex.py \
	  --src $(TREX_SRC) --sha 27e0153b5 \
	  --out backend/trex_web_gui/_vendor --patches backend/vendor/patches --pin backend/vendor/TREX_PIN

dev:
	$(UV) trex-web-gui serve --dev

wire-fixtures:    # re-record the JSON files in frontend/src/__fixtures__/ from the stateful fake
	WRITE_WIRE_FIXTURES=1 $(PYTEST) -q tests/contract/test_wire_fixtures.py

deferred:         # every open ledger entry is in DEFERRED.md (run at a final gate; scripts/deferred.py sync adds them)
	python3 -I scripts/deferred.py check

citations:        # resolve every file:line citation and check its quoted snippet (part of make check since 2026-10-18)
	$(UV) python -I -B scripts/check_citations.py

parity:           # the v1.0 parity checklist against the inventory (PARITY_GATE=1: fail on any ?)
	$(UV) python -I -B scripts/parity.py check --plans $(TREX_PLANS) $(if $(PARITY_GATE),--gate,)

parity-sync:      # regenerate docs/parity-v1.0.md from the inventory, keeping the Evidence column
	$(UV) python -I -B scripts/parity.py sync --plans $(TREX_PLANS)

docstrings:        # every project function has a docstring or a real comment
	$(UV) python -I -B scripts/check_docstrings.py

api-docs:        # regenerate docs/api.md from the routes' docstrings and models (make check fails when it is stale)
	$(UV) python -I -B scripts/gen_api_docs.py
