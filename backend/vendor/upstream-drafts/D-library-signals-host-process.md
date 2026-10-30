<!-- DRAFT for review, not submitted. Target: github.com/cisco-system-traffic-generator/trex-core/issues
     (enhancement). trex-web-gui patch 05. See ../patches.md. -->

**Title:** Python client sends SIGTERM/SIGINT to its own process; this breaks applications that embed it

**Labels (suggested):** enhancement, python-api

### Summary

The client library signals the process it runs in, in two places:

1. **SIGTERM on subscriber crash.** In `common/trex_client.py:234-238`, when the async subscriber thread
   crashes, the client logs "Terminating due to subscriber crash" and calls
   `os.kill(os.getpid(), signal.SIGTERM)`.
2. **SIGINT on connection loss.** In `common/trex_conn.py:116-119`, when the connection is lost while an API
   call is in progress (`sigint_on_conn_lost`, which `client_api` enables on entry to every call), it calls
   `os.kill(os.getpid(), signal.SIGINT)` to interrupt the main thread.

That suits the interactive console. But in a program that embeds the client (a web service, a test framework,
a GUI), these calls hit the whole host:
- the SIGTERM kills every other part of the program;
- the SIGINT raises `KeyboardInterrupt` in whatever the main thread happens to be running, which need not
  be the TRex call.

### Version

v3.08 (`master` at 27e0153b5).

### Request

Let the embedder choose. For example, a client option (or a constructor argument such as
`signal_on_fatal=True`, defaulting to today's behaviour) that, when off:
- raises or records the subscriber failure and marks the connection as lost, without killing the process;
- skips the SIGINT and lets the pending RPC fail with the connection-lost error it already reports.

In our vendored copy we simply removed both `os.kill` calls. We handle loss from the client's own
`connection lost` state and the RPC errors. A configurable version would keep the console behaviour intact.

### Reproduce

```python
from trex.stl.api import STLClient
c = STLClient(verbose_level='none')
try:
    raise ValueError('seq gap')
except ValueError as e:
    c.ctx.event_handler.on_event('subscriber crashed', e)
print('alive')      # never printed: the process exits on SIGTERM (exit status -15)
```

---
*Found while embedding the TRex Python client in a GUI. The analysis was prepared with an AI coding assistant
(Claude) and reviewed by me.*

<!-- Before submitting:
  - [ ] Reviewed and agreed. Decide whether to offer a PR for the option; our patch only deletes the calls,
        which upstream would likely not accept as is.
-->
