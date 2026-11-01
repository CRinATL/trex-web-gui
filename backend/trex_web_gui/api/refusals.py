"""The refusal codes of the three refusal families (session, daemon, bundle), the shared table of
docs/api.md. A route that can reach the mapping site of a family declares the family
(`answers((409, "session"))`) instead of each code.

Mapping sites: `CommandRefused` is 409 (422 for `bad_request`) at `run` in api/app.py; `DaemonRefused` is 409
at `_answer` in api/daemon.py; `BundleError` is 422 in api/bundle.py. A code raised by two families has a row
for each. Keys are (family, code). `MonitorHostRefused` (monitor/session.py) has
no code and its route declares its own 422 `invalid`, so it is not in this table.

tests/unit/test_api_errors.py keeps the table equal to the raise sites found by
scripts/check_api_errors.py."""
from trex_web_gui.api.declared import Family

REFUSALS: dict[tuple[Family, str], tuple[int, str]] = {
    # CommandRefused (session)
    ("session", "bad_port"): (409, "a port the server does not have, or a profile not loaded "
                                  "for a port"),
    ("session", "bad_request"): (422, "no attribute to change"),
    ("session", "capture_busy"): (409, "the capture is being saved, is the live monitor's, or belongs to a "
                                    "TRex service"),
    ("session", "capture_slots"): (409, "the server's capture slots are used up (one is kept for ping, ARP "
                                      "and scan)"),
    ("session", "endpoint_capture"): (409, "the capture streams to an endpoint, so its packets are not held "
                                         "here"),
    ("session", "ipv6_unsupported"): (409, "IPv6 configuration needs the linux_based stack; "
                                         "the server runs legacy"),
    ("session", "monitor_active"): (409, "a capture monitor is already running"),
    ("session", "no_monitor"): (409, "no capture monitor is running"),
    ("session", "not_active"): (409, "a port's profile is not one this backend controls"),
    ("session", "not_connected"): (409, "not connected to a TRex server"),
    ("session", "not_owned"): (409, "the port is owned by another client; acquire it first"),
    ("session", "port_active"): (409, "the port is transmitting; stop traffic first"),
    ("session", "port_busy"): (409, "the port is busy applying a configuration, or pinging or scanning"),
    ("session", "read_only"): (409, "the session is read-only (observer mode)"),
    ("session", "service_filtered"): (409, "the port is in filtered service mode; turn service mode on"),
    ("session", "service_off"): (409, "the ports are not in service mode; turn it on first"),
    ("session", "spool_error"): (409, "the capture file could not be written"),
    ("session", "spool_unavailable"): (409, "this backend has no capture directory"),
    ("session", "wireshark_unavailable"): (409, "Wireshark cannot be launched"),
    # DaemonRefused (daemon)
    ("daemon", "not_connected"): (409, "not connected to a TRex daemon"),
    ("daemon", "already_running"): (409, "TRex is already running on the daemon"),
    ("daemon", "busy"): (409, "another daemon operation is in progress"),
    ("daemon", "confirm_required"): (409, "the operation needs `confirm`"),
    ("daemon", "foreign_trex"): (409, "a TRex this daemon did not start is running"),
    ("daemon", "in_use"): (409, "a port is in use by another owner"),
    ("daemon", "invalid_config"): (409, "the config to push is invalid"),
    ("daemon", "invalid_profile"): (409, "the profile is not a stock profile, or does not load, or changed "
                                       "during the upload"),
    ("daemon", "not_ours"): (409, "the run is not one this backend started, so Stop does not stop it"),
    ("daemon", "owners_unknown"): (409, "who owns the ports cannot be told; acknowledge to go on"),
    ("daemon", "plan_changed"): (409, "the target or files changed since the preview"),
    ("daemon", "reserved"): (409, "the daemon is reserved"),
    # BundleError (bundle)
    ("bundle", "bad_request"): (422, "a bundle import mode that is not allowed"),
    ("bundle", "invalid_bundle"): (422, "a section of the bundle does not validate"),
    ("bundle", "unsupported_bundle"): (422, "not a bundle of this format and version"),
}
