<!-- DRAFT for review, not submitted. Target: github.com/cisco-system-traffic-generator/trex-core/issues.
     trex-web-gui patches 02, 03, 04, 07. Mention open PR #1196 (partial overlap with item 3). See ../patches.md. -->

**Title:** Python client: four Python 3.12/3.13 and PyYAML 6 incompatibilities

**Labels (suggested):** bug, python-api

### Summary

Running the v3.08 Python client on Python 3.12/3.13, or with a current PyYAML, hits four separate problems.
Each one has a small fix. Paths are under `scripts/automation/trex_control_plane/interactive/trex/` unless
noted.

**1. `STLProfile.load_yaml` uses `yaml.load()` without a Loader** (`stl/trex_stl_streams.py:1105`)
- **PyYAML ≥ 6:** `load() missing 1 required positional argument: 'Loader'`, so YAML profiles cannot be
  loaded.
- **Bundled PyYAML 3.11:** the profile is loaded with the unsafe loader, so a YAML file can construct
  arbitrary Python objects.
- **Fix:** `yaml.safe_load(f)`. Profiles are plain data, and the round trip `to_json` → `yaml.dump` →
  `load_yaml` still matches with the fix.

**2. Bundled Scapy 2.4.3 imports `cgi`, which Python 3.13 removed** (PEP 594)
- **Where:** `scripts/external_libs/scapy-2.4.3/scapy/themes.py:14,366`.
- **Effect:** `import scapy.themes` fails with "No module named 'cgi'".
- **Fix:** current Scapy already uses `html.escape`. The minimal local fix is `import html` plus
  `html.escape(conf.prompt, quote=False)`. Upgrading the bundled Scapy would fix it for good.

**3. Invalid escape sequences in regex literals** (`SyntaxWarning` on Python ≥ 3.12; a future Python makes
them errors)
- `common/trex_subscriber.py`: `'(.*)\-([0-8])'`.
- `scapy_server/scapy_service.py`: `"[\s]"`, and the `IPField` pattern with `\.`.
- `utils/common.py`: `'(\d+)'`.
- `utils/parsing_opts.py`: `"^(\d+(\.\d+)?)([m|h]?)$"`.
- **Fix:** make them raw strings. That removes all 8 warnings in the client package. Open PR #1196 fixes the
  `utils/common.py` one, plus two scripts outside the client.

**4. `e.message` in the Scapy service** (`scapy_server/scapy_service.py:893,895`)
- **Effect:** Python 3 exceptions have no `.message`, so an error in the field-engine builder raises
  `AttributeError: 'AssertionError' object has no attribute 'message'` instead of returning the error
  string.
- **Fix:** `str(e)`. This is unrelated to #1219 / PR #1220, which fix template bytes in the same file.

### Version

v3.08 (`master` at 27e0153b5); Python 3.12 and 3.13; PyYAML 6.

### Patches

We carry these as four small patches in our vendored copy. Each has a test that fails without it, and we can
attach them as diffs or open one PR per item, whichever the maintainers prefer.

---
*Found while embedding the TRex Python client in a GUI. The analysis and patches were prepared with an AI
coding assistant (Claude) and reviewed by me.*

<!-- Before submitting:
  - [ ] Reviewed and agreed.
  - [ ] Attach the diffs from backend/vendor/patches/02, 03, 04, 07 with their paths rewritten from
        a/trex/… to a/scripts/automation/trex_control_plane/interactive/trex/… (03 is under
        scripts/external_libs/ upstream).
  - [ ] Check whether PR #1196 has merged in the meantime and trim item 3 to match.
-->
