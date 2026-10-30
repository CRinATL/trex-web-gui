<!-- DRAFT for review, not submitted. Target: github.com/cisco-system-traffic-generator/trex-core/issues.
     trex-web-gui patch 06. Low priority: dormant on v3 servers. See ../patches.md. -->

**Title:** `TRexSubscriber` defines `handle_flow_stats_update` twice; `handle_latency_stats_update` is missing

**Labels (suggested):** bug, python-api

### Summary

`common/trex_subscriber.py` defines `handle_flow_stats_update` twice. The second definition, which emits
`"latency stats update"`, replaces the first. `__dispatch` calls `self.handle_latency_stats_update(...)` for
`latency_stats`, and that method does not exist.

As a result:
- a `latency_stats` publish raises AttributeError in the subscriber thread;
- a `flow_stats` publish is reported to the event handler as "latency stats update".

### Version

v3.08 (`master` at 27e0153b5).

### Where

`scripts/automation/trex_control_plane/interactive/trex/common/trex_subscriber.py`:
- `:376-381`: dispatch on `flow_stats` and `latency_stats`.
- `:401`: first definition, "flow stats update".
- `:405`: second definition, same name, "latency stats update".

### Impact

As far as we can see, this is dormant with current servers. In interactive mode `publish_async_data`
(`src/main_dpdk.cpp`) publishes only the global stats, so neither branch runs. It is a trap for anyone who
re-enables those publishes. Linters flag it as a redefinition (F811).

### Suggested fix

```diff
--- a/scripts/automation/trex_control_plane/interactive/trex/common/trex_subscriber.py
+++ b/scripts/automation/trex_control_plane/interactive/trex/common/trex_subscriber.py
@@ -402,7 +402,7 @@
         self.ctx.event_handler.on_event("flow stats update", data, baseline)
 
 
-    def handle_flow_stats_update (self, data, baseline):
+    def handle_latency_stats_update (self, data, baseline):
         self.ctx.event_handler.on_event("latency stats update", data, baseline)
```

---
*Found while embedding the TRex Python client in a GUI. The analysis and patch were prepared with an AI coding
assistant (Claude) and reviewed by me.*

<!-- Before submitting:
  - [ ] Reviewed and agreed. The "dormant" claim rests on reading publish_async_data in main_dpdk.cpp and
        trex_stf.cpp at the pin; ASTF and STF paths were not exhaustively checked.
-->
