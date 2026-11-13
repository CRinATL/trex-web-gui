"""What the test fakes assume without a recording, and the check that keeps the list honest.

A fake (`fake_emu.py`, `fake_trex.py`, `fake_daemon.py`) answers from recorded fixtures where it can. Where it
cannot, it invents a text or a behaviour, and each such assumption is an `Entry` in `REGISTER` below, keyed
by an id matching `^(emu|trex|daemon)-[a-z0-9-]+$`. `tests/unit/test_fake_register.py` runs under
`make check` and fails on an assumption that is not registered and tagged.

The shape of an entry:

- `fake` is "fake_emu", "fake_trex" or "fake_daemon".
- `kind` is "text" (a reply text), "behaviour" (what the fake does where the server was not recorded),
  "model" (a simplification of the server) or "hook" (a test hook, never a claim about the server).
- `assumes` is one sentence on what the fake assumes.
- `text` is the skeleton of the reply literal the entry covers. It is required for "text", optional for "hook"
  (a hook that answers a literal, such as `--fail`'s text, carries it), and absent for "behaviour" and
  "model".
  A text or hook entry's `text` must equal the skeleton of some reply literal in its fake, and must not be
  recorded: recording a text removes its entry.
- `settle` takes exactly one of three forms. `real:tests/real/<file>.py::<test_name>`: a real test pins the
  behaviour the fake copies. `open: <why, and what would settle it>`: nothing settles it yet. `hook: <flag or
  signal>`: a test hook.

A reply literal in a fake's source is one of: a `str` constant or f-string that is the value of a "message" or
"specific_err" key in a dict display; the first argument of a `ValueError(...)` call; the value of a
module-level assignment to an UPPER_CASE name of a `str` constant. Its skeleton is its constant text with each
f-string field written `{}`. It is recorded when its skeleton, with each `{...}` placeholder read as `.+?`,
fullmatches a whole string value in some `tests/fixtures/**/*.json` or `*.jsonl` file. A reply literal passes
the check when it is recorded, or when any entry of that fake has `text` equal to its skeleton, whatever the
entry's kind. A text the fake reads from a fixture has no entry.

A tag is the literal `[U:<id>]`. A source line that says made up, not recorded, invented, assumed or
unverified carries a tag on the same line, and so does each `- ` item of the `Known divergences from the real
server:` section of the fake's module docstring. Every tag names an entry of its fake, and every entry is
tagged at least once.

To register an assumption, add an `Entry` here and tag the fake's line or divergences item `[U:<id>]`.

Run `uv run --project backend python -I -B tests/fake/unrecorded.py` for the count of entries per fake and
settle form."""

from dataclasses import dataclass

FAKE_NAMES = ("fake_emu", "fake_trex", "fake_daemon")
SETTLE_FORMS = ("real", "open", "hook")


@dataclass(frozen=True)
class Entry:
    """One assumption of a fake that no recording backs."""

    fake: str
    kind: str
    assumes: str
    settle: str
    text: str | None = None


REGISTER: dict[str, Entry] = {
    "emu-hook-injected-failure": Entry(
        "fake_emu",
        "hook",
        "--fail answers a -32600 error with the text ' injected failure ', which no server sends.",
        "hook: --fail METHOD=N",
        text=" injected failure ",
    ),
    "emu-client-info-synth": Entry(
        "fake_emu",
        "model",
        "ctx_client_get_info copies the recorded client record and synthesises its ipv4 from the MAC.",
        "open: a known MAC's record copies the recorded CLIENT_INFO and synthesises ipv4, not the address "
        "the client was added with; no E3 step reads a known MAC",
    ),
    "emu-key-rendering": Entry(
        "fake_emu",
        "model",
        "An error text that names the key it failed on repeats the recorded text with the asked vport (and "
        "colon-form MAC) swapped in.",
        "open: only the vport is substituted; the VLAN part of the recorded rendering stays as recorded",
    ),
    "emu-client-remove-atomic": Entry(
        "fake_emu",
        "behaviour",
        "ctx_client_remove with several MACs removes none when one of them is unknown.",
        "open: E3 sent one MAC per ctx_client_remove; a step with a known and an unknown MAC would record it",
    ),
    "emu-partial-write-epoch": Entry(
        "fake_emu",
        "behaviour",
        "A ctx_add, ctx_remove or ctx_client_add that answers an error after part of it took effect "
        "bumps the epoch once, and one that changed nothing bumps none.",
        "open: E3's listings were reset reads and never saw an epoch; a recorder step that opens a cursor "
        "before a refused partial write and pages it after would record it",
    ),
    "emu-remove-repeat-error": Entry(
        "fake_emu",
        "behaviour",
        "A tunnel repeated within one ctx_remove answers the unknown-tunnel error for the repeat.",
        "open: E3 sent no repeated ctx_remove; a step naming one tunnel twice would record it",
    ),
    "emu-remove-keeps-before-refusal": Entry(
        "fake_emu",
        "behaviour",
        "A ctx_remove refused for a namespace that holds clients, when it is not the first tunnel, "
        "leaves the removals before it done.",
        "open: E3 sent only a single-tunnel removal with clients; a step with a clean tunnel before a "
        "populated one would record it",
    ),
    "emu-restart-model": Entry(
        "fake_emu",
        "behaviour",
        "A restart gives a new api_h and empty state, and a request with the old api_h answers -32602.",
        "real:tests/real/test_emu_real.py::test_restart_of_trex_is_detected_and_a_new_connection_sees_an_empty_server",
    ),
    "emu-standalone-recording": Entry(
        "fake_emu",
        "behaviour",
        "The fake's texts were recorded from standalone trex-emu; the app talks to TRex --emu.",
        "real:tests/real/test_emu_real.py::test_the_recorded_negatives_hold_under_trex",
    ),
    "emu-meta-ignores-mask": Entry(
        "fake_emu",
        "behaviour",
        "A meta counter request ignores its mask.",
        "real:tests/real/test_emu_real.py::test_the_server_ignores_mask_on_a_meta_counter_request",
    ),
    "emu-counters-static": Entry(
        "fake_emu",
        "model",
        "Counters never advance and one recorded table serves every namespace.",
        "open: the fake sends no packets and seeds no per-namespace counters (DEFERRED p7-emu-218)",
    ),
    "emu-ipfix-no-panic": Entry(
        "fake_emu",
        "behaviour",
        "Removing a namespace that loaded the ipfix plugin answers normally.",
        "open: the fake never dies; --hang ctx_remove models the unanswered request; E1 measured the crash "
        "(docs/evidence/2026-11-05-p7-prevention-study/e1/e1-report.md) and R33 refuses the profiles that "
        "cause it",
    ),
    "trex-xstats-short-ports": Entry(
        "fake_trex",
        "behaviour",
        "A port's xstats list can be shorter than another port's, or shrink after its names were read.",
        "open: seen 2026-11-06 on a server whose ports mix physical and virtual functions (a reset failed "
        "'Length of get_xstats_names: 91 and get_port_xstats_values: 84'); the per-port lists were not "
        "recorded, and a probe of such a server would record them",
    ),
    "trex-link-led-fc-accepted": Entry(
        "fake_trex",
        "behaviour",
        "Link, LED and flow-control changes are accepted.",
        "open: the net_af_packet guest reports them unsupported; "
        "a guest port that supports them would record it",
    ),
    "trex-rx-filter-mode": Entry(
        "fake_trex",
        "model",
        "rx_filter_mode is not mapped to a status key.",
        "open: P2 never sends it; a record_p2.py step setting rx_filter_mode would record the status key",
    ),
    "trex-api-h-class-common": Entry(
        "fake_trex",
        "behaviour",
        "An api_h mismatch always names class 'common', on any method.",
        "open: recorded on a common-class method only; a record_p2.py step on a port method would record it",
    ),
    "trex-inline-stream": Entry(
        "fake_trex",
        "model",
        "validate_result and the stream shapes come from one inline stream.",
        "open: a modelling choice",
    ),
    "trex-hook-seq-gap": Entry(
        "fake_trex",
        "hook",
        "The injected seq gap is served to every get_async_events session_id.",
        "hook: --seq-gap-at-start, SIGUSR1",
    ),
    "trex-restart-applied-late": Entry(
        "fake_trex",
        "model",
        "A SIGUSR2 restart is applied by the request loop before the next message.",
        "open: the fake's mechanics, not a server claim",
    ),
    "trex-mul-echo": Entry(
        "fake_trex",
        "behaviour",
        "start_traffic and update_traffic echo the requested mul, and ts is wall-clock.",
        "open: only pps/abs recorded; a record_p2.py step with another multiplier would record it",
    ),
    "trex-state-preconditions": Entry(
        "fake_trex",
        "behaviour",
        "Start while TX, pause unless TX, resume unless PAUSE and update while idle are accepted.",
        "open: no text recorded; four record_p2.py steps would record them",
    ),
    "trex-filtered-service": Entry(
        "fake_trex",
        "behaviour",
        "Filtered service mode is accepted.",
        "open: refused without --software; a guest without it would record the text",
    ),
    "trex-stats-model": Entry(
        "fake_trex",
        "model",
        "Stats have no ramp, pair ports by pid ^ 1 and lose nothing.",
        "open: a modelling choice",
    ),
    "trex-cpu-idle": Entry(
        "fake_trex",
        "model",
        "cpu_util, bw_per_core and rx_cpu_util stay at the recorded idle values.",
        "open: a modelling choice",
    ),
    "trex-reverse-direction": Entry(
        "fake_trex",
        "behaviour",
        "Port 1 transmitting shows as port 0 Rx by pid ^ 1.",
        "open: only port 0 transmitting was recorded; "
        "a record_p3.py step with port 1 transmitting would record it",
    ),
    "trex-pgid-model": Entry(
        "fake_trex",
        "model",
        "PGID stats follow the fake's streams and port state with no ramp or loss.",
        "open: a modelling choice",
    ),
    "trex-text-pgid-too-many": Entry(
        "fake_trex",
        "text",
        "The server's text for a get_pgid_stats request of too many ids.",
        "open: (src) trex_stl_fs.cpp dump_json; a record_p3a.py request for 1153 ids would record it",
        text="Asking information for too many packet group IDs. Max allowed is {}",
    ),
    "trex-text-pgid-all-too-many": Entry(
        "fake_trex",
        "text",
        "The server's text for an all-ids get_pgid_stats with too many active ids.",
        "open: (src) trex_stl_fs.cpp dump_json; a record_p3a.py get_pgid_stats of no ids with more than 1152 "
        "active ids would record it",
        text="Asking information for all packet group IDs. There are currently {} active IDs . "
        "Max allowed is {}. Need to limit query size.",
    ),
    "trex-utilization-replay": Entry(
        "fake_trex",
        "model",
        "get_utilization replays one recorded answer in every state.",
        "open: a modelling choice",
    ),
    "trex-hook-manual-clock": Entry(
        "fake_trex",
        "hook",
        "Counters integrate a clock that moves only on the fake_advance_clock RPC.",
        "hook: --manual-clock, fake_advance_clock",
    ),
    "trex-hook-server-version": Entry(
        "fake_trex",
        "hook",
        "get_version reports the version string given on the command line.",
        "hook: --server-version",
    ),
    "trex-link-event": Entry(
        "fake_trex",
        "behaviour",
        "No link-change event is emitted.",
        "open: (src) main_dpdk.cpp; a polled link change on a hardware guest would record it",
    ),
    "trex-unknown-profile": Entry(
        "fake_trex",
        "behaviour",
        "stop, pause and resume of an unknown profile answer {}.",
        "open: (src) valide_profile throws; unreachable through the client, so only a raw RPC step "
        "in a record script naming an unknown profile would record it",
    ),
    "trex-port-event-type": Entry(
        "fake_trex",
        "behaviour",
        "Port events are typed by the method that caused them.",
        "open: (src) the transition map in trex_stl_port.cpp; a record_p2.py step per (old, new) "
        "state transition would record the event types",
    ),
    "trex-p3b-imitated-texts": Entry(
        "fake_trex",
        "behaviour",
        "The set_l3_mode, conf_ipv6 and set_vlan_cfg refusal texts imitate the recorded set_l2_mode one.",
        "open: a record_p3b.py step per command outside IDLE would record them",
    ),
    "trex-p3b-validation": Entry(
        "fake_trex",
        "model",
        "A malformed MAC, address or VLAN tag is accepted.",
        "open: a modelling choice",
    ),
    "trex-p3b-linux-wip": Entry(
        "fake_trex",
        "behaviour",
        "On linux_based, set_l2, set_l3, set_vlan and conf_ipv6 answer at once with no WIP ticket.",
        "open: the ticket is recorded (tests/fixtures/p3b-v6) but not modelled; replaying it in the fake, "
        "with the polled get_async_results, would settle it",
    ),
    "trex-push-pkts": Entry(
        "fake_trex",
        "behaviour",
        "push_pkts answers the unknown-method error.",
        "open: never run against the fake; a record_p3b.py step sending push_pkts would record the answer",
    ),
    "trex-set-vlan-check-order": Entry(
        "fake_trex",
        "behaviour",
        "set_vlan checks the port state before the tag count (DEFERRED P3b-32).",
        "open: (src) the server parses tags first; a vm/record_p3b.py step sending three tags outside IDLE "
        "would record which refusal comes first",
    ),
    "trex-bad-port-raises": Entry(
        "fake_trex",
        "model",
        "A bad port_id or malformed src_ipv6 raises in the fake (DEFERRED P3b-33).",
        "open: no text recorded; a vm/record_p3b.py step with a bad port_id would record it",
    ),
    "trex-capture-no-bpf": Entry(
        "fake_trex",
        "model",
        "No BPF filter is evaluated.",
        "open: a modelling choice",
    ),
    "trex-capture-synth": Entry(
        "fake_trex",
        "model",
        "Captured packets are synthesised round-robin on the fake clock.",
        "open: a modelling choice",
    ),
    "trex-capture-service-af-packet": Entry(
        "fake_trex",
        "behaviour",
        "A Tx capture of stream traffic needs service mode.",
        "open: measured on af_packet only; a record_p3c.py run against a hardware guest would settle it",
    ),
    "trex-capture-unrecorded-refusals": Entry(
        "fake_trex",
        "model",
        "An unknown command, a missing parameter and an endpoint start raise HarnessError.",
        "open: the server's texts are not recorded; a record_p3c.py step per refusal would record them",
    ),
    "trex-capture-xstats": Entry(
        "fake_trex",
        "model",
        "xstats follow the port counters.",
        "open: a modelling choice",
    ),
    "daemon-exit-lag-listing": Entry(
        "fake_daemon",
        "behaviour",
        "--exit-lag keeps an ended run's process listed by get_trex_cmds for S seconds after the status "
        "is Idle.",
        "open: read from trex_launch_thread.py `run` (SIGUSR1, then Idle at once); nothing recorded it, "
        "and a real test would need a TRex slow to exit",
    ),
    "daemon-foreign-after-start": Entry(
        "fake_daemon",
        "behaviour",
        "--foreign-after-start lists a hand-started TRex only once a start_trex has succeeded.",
        "open: a TRex started by hand beside the run is modelled; no real test starts one mid-run",
    ),
    "daemon-foreign-ends-with-run": Entry(
        "fake_daemon",
        "behaviour",
        "--foreign-ends-with-run empties the foreign process from get_trex_cmds the moment the run ends.",
        "open: modelled; the real list lags the status, which --exit-lag models",
    ),
    "daemon-trex-log-after-start": Entry(
        "fake_daemon",
        "behaviour",
        "After a start_trex has succeeded, get_trex_log reads normally (the recorded p5 body).",
        "real:tests/real/test_daemon_real.py::test_daemon_push_start_stop_cycle",
    ),
}


def main() -> None:
    """Print one line per fake and settle form with the number of entries."""
    for fake in FAKE_NAMES:
        for form in SETTLE_FORMS:
            count = sum(1 for e in REGISTER.values() if e.fake == fake and e.settle.startswith(form + ":"))
            print(f"{fake} {form}: {count}")


if __name__ == "__main__":
    main()
