<!-- DRAFT for review, not submitted. Target: github.com/cisco-system-traffic-generator/trex-core/issues
     (plus an optional PR with the diff below). trex-web-gui patch 08. See ../patches.md for the rules. -->

**Title:** `set_port_attr(link_up=…, led_on=…, flow_ctrl=…)` is silently ignored: `Port.set_attr` reads different keyword names

**Labels (suggested):** bug, python-api

### Summary

`TRexClient.set_port_attr()` passes `link_up`, `led_on` and `flow_ctrl` to `Port.set_attr()`. But
`Port.set_attr()` looks for `link_status`, `led_status` and `flow_ctrl_mode`. None of the three attributes
reaches the server, and the call still returns success. So link up/down, LED and flow-control changes made
through the Python API (and the console's `portattr`) do nothing.

### Version

v3.08 (`master` at 27e0153b5). The mismatch dates from 39a62671c (2018-05-24, "consolidation - refactor of
Python library to allow STL/ASTF", v2.42).

### Where

`scripts/automation/trex_control_plane/interactive/trex/`:
- `common/trex_client.py:2153-2204`: `set_port_attr(…, link_up=None, led_on=None, flow_ctrl=None, …)`
  validates those names and calls `self.ports[port].set_attr(link_up=link_up, led_on=led_on, flow_ctrl=flow_ctrl, …)`.
- `common/trex_port.py:500-507`: `set_attr(**kwargs)` reads `kwargs.get('link_status')`,
  `kwargs.get('led_status')` and `kwargs.get('flow_ctrl_mode')`.

`STLClient.reset(restart=True)` restores the link through the same path, so it is affected too.

### Reproduce (no server needed)

```python
from trex.stl.trex_stl_port import STLPort
from trex.common.trex_types import RC_OK
p = object.__new__(STLPort)
p.port_id, p.handler, sent = 0, 'h', []
p.is_acquired = lambda: True
p.transmit = lambda method, params: sent.append(params) or RC_OK()
p.sync = lambda: RC_OK()
p.set_attr(link_up=False, led_on=True, flow_ctrl=2)
print(sorted(sent[0]['attr']))
```

Actual: `[]`, so the `set_port_attr` RPC is sent with an empty `attr`.
Expected: `['flow_ctrl_mode', 'led_status', 'link_status']`.

### Suggested fix

Read the names the callers pass. The RPC's JSON keys stay as they are:

```diff
--- a/scripts/automation/trex_control_plane/interactive/trex/common/trex_port.py
+++ b/scripts/automation/trex_control_plane/interactive/trex/common/trex_port.py
@@ -497,14 +497,14 @@
         if kwargs.get('multicast') is not None:
             json_attr['multicast'] = {'enabled': kwargs['multicast']}
 
-        if kwargs.get('link_status') is not None:
-            json_attr['link_status'] = {'up': kwargs['link_status']}
+        if kwargs.get('link_up') is not None:
+            json_attr['link_status'] = {'up': kwargs['link_up']}
 
-        if kwargs.get('led_status') is not None:
-            json_attr['led_status'] = {'on': kwargs['led_status']}
+        if kwargs.get('led_on') is not None:
+            json_attr['led_status'] = {'on': kwargs['led_on']}
 
-        if kwargs.get('flow_ctrl_mode') is not None:
-            json_attr['flow_ctrl_mode'] = {'mode': kwargs['flow_ctrl_mode']}
+        if kwargs.get('flow_ctrl') is not None:
+            json_attr['flow_ctrl_mode'] = {'mode': kwargs['flow_ctrl']}
```

---
*Found while embedding the TRex Python client in a GUI. The analysis and patch were prepared with an AI coding
assistant (Claude) and reviewed by me; we have carried this fix in our vendored copy since 2026-10-16.*

<!-- Before submitting:
  - [ ] Reviewed and agreed with every claim above.
  - [ ] Optionally re-run the repro on an unpatched v3.08 client.
  - [ ] If opening a PR: commit with `git commit -s` (your Signed-off-by) and add
        `Assisted-by: Claude:claude-opus-5-5`; put the repro in a unit test if upstream wants one.
-->
