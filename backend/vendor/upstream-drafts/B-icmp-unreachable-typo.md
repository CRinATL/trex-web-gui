<!-- DRAFT for review, not submitted. Target: github.com/cisco-system-traffic-generator/trex-core/issues
     (plus an optional PR with the diff below). trex-web-gui patch 09. See ../patches.md for the rules. -->

**Title:** `PINGRecord` raises AttributeError on ICMP destination-unreachable (`ICMP_TYPE_DEST_UNREACBLE` typo)

**Labels (suggested):** bug, python-api, services

### Summary

In `ServiceICMP.PINGRecord.__init__`, the destination-unreachable branch assigns
`self.state = self.ICMP_TYPE_DEST_UNREACBLE`. That is a misspelling of the class constant
`ICMP_TYPE_DEST_UNREACHABLE` (= 3). So an unreachable reply raises AttributeError instead of being recorded,
and `ping_ip()` fails instead of printing "Destination host unreachable". `__str__` already uses the correct
name.

### Version

v3.08 (`master` at 27e0153b5).

### Where

`scripts/automation/trex_control_plane/interactive/trex/common/services/trex_service_icmp.py`:
- `:138`: the constant is defined as `ICMP_TYPE_DEST_UNREACHABLE = 3`.
- `:161`: compared using the correct name.
- `:163`: assigned using the misspelled name.
- `:175`: read back using the correct name.

### Reproduce (no server needed)

```python
from types import SimpleNamespace as NS
from trex.common.services.trex_service_icmp import ServiceICMP
r = ServiceICMP.PINGRecord({'ICMP': NS(type=3), 'IP': NS(src='<responder>')}, 0.0, 0.1)
print(r.state, r)
```

Actual: `AttributeError: … has no attribute 'ICMP_TYPE_DEST_UNREACBLE'`.
Expected: `3 Reply from <responder>: Destination host unreachable`.

In practice the reply reaches the record only when the filter's `lookup` (`:38-54`) matches it. That lookup
keys on (src IP, ICMP id, seq, VLANs) read from the reply's outer ICMP header, and the bundled Scapy 2.4.3
leaves id and seq absent for type 3, so this is a rare path. When it is reached, it crashes.

### Suggested fix

```diff
--- a/scripts/automation/trex_control_plane/interactive/trex/common/services/trex_service_icmp.py
+++ b/scripts/automation/trex_control_plane/interactive/trex/common/services/trex_service_icmp.py
@@ -160,7 +160,7 @@
 
             if scapy_pkt['ICMP'].type == self.ICMP_TYPE_DEST_UNREACHABLE:
                 self.responder_ip = scapy_pkt['IP'].src
-                self.state        = self.ICMP_TYPE_DEST_UNREACBLE
+                self.state        = self.ICMP_TYPE_DEST_UNREACHABLE
                 return
```

---
*Found while embedding the TRex Python client in a GUI. The analysis and patch were prepared with an AI coding
assistant (Claude) and reviewed by me.*

<!-- Before submitting:
  - [ ] Reviewed and agreed with every claim above.
  - [ ] If opening a PR: `git commit -s` plus `Assisted-by: Claude:claude-opus-5-5`.
-->
