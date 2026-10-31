# trex-web-gui: the GUI guide

New here? Start with [your first stream](howto-first-stream.md), a short walk-through with shots.

A user's guide to the web GUI. It follows the order you work in: log in, connect, take ports, load a profile,
start and stop traffic, watch the dashboards, configure and probe ports, capture, build profiles in Studio and
packets in the packet editor, watch a publisher on the Monitor, drive the TRex daemon, and keep settings and
setups. Every control is named as it appears on screen, and every refusal text is quoted from the code.

Screenshots are the inventory's (`gui-inventory/screens/`, shot at 1400x900 in the Light theme against the
test guest; the numbers below are theirs). The inventory itself (`gui-inventory/README.md`) is a screen-by-screen
record of what each shot shows; this guide is about what the controls do. The shots were taken on 2026-10-30.

The backend holds one TRex session for the whole GUI. Every browser tab that is logged in sees the same
session, the same ports and the same captures; the GUI is single-user by design.

How the backend is started, the login URL and the SSH tunnel are in the [README](../README.md). The HTTP API behind
every button is in [`api.md`](api.md).

## Contents

1. [Getting in](#1-getting-in): the login URL, the top bar, the status bar, shortcuts, themes
2. [Connect to TRex](#2-connect-to-trex)
3. [Control page](#3-control-page): the devices tree, the toolbar, the context menus, Start all
4. [A port's panel](#4-a-ports-panel): Control, Config, Traffic, Counters, Tools
5. [Saved test setups](#5-saved-test-setups)
6. [Dashboard](#6-dashboard)
7. [Events](#7-events)
8. [Capture](#8-capture): Live, the packet viewer, Recorders, Saved files, LLDP, Wireshark
9. [Studio](#9-studio): the profile library, import, the stream table, properties, the packet tab, Simulate
10. [Packet editor](#10-packet-editor)
11. [Monitor](#11-monitor)
12. [TRex daemon dialog](#12-trex-daemon-dialog)
13. [Settings](#13-settings)
14. [Why is this disabled?](#14-why-is-this-disabled-the-common-reasons)
15. [What is kept, and for how long](#15-what-is-kept-and-for-how-long)
16. [Keyboard shortcuts](#16-keyboard-shortcuts)
17. [Screenshot index](#17-screenshot-index)

---

## 1. Getting in

### 1.1 The login URL

`trex-web-gui serve` prints `Open: http://127.0.0.1:8765/api/auth/login?token=...`. Opening that URL sets the
session cookie and sends the browser to `/`, which the app redirects to `/control`. The token lives as long as
the backend process: after a restart, open the new URL.

A browser without the cookie sees the **Not authorised** page instead of the app: the heading "Not authorised"
and one sentence, "Open the URL printed by `trex-web-gui serve` on the backend host; through a tunnel, the same
URL with `localhost`." Nothing else is on it ([01](gui-inventory/screens/01-not-authorised.png)).

Between the cookie check and the first message from the backend the page shows only "Connecting…".

### 1.2 The top bar

On every page: the name **trex-web-gui** at the left, then the links **Control**, **Dashboard**, **Capture**,
**Studio**, **Monitor**, **Events**, **Settings**. The current page is underlined. **Events** carries a red
badge with the number of errors and warnings that arrived while the Events page was not open; opening Events
clears it.

The Control, Capture, Studio and Packet editor pages stay loaded while you are on another page, so their
selection, forms and tables are as you left them when you come back. The Dashboard keeps its tab, chart
interval and layout too (they reset on a new connection).

A backend started with `--monitor-only` shows a top bar with only **Monitor** and **Settings**, and every
other address goes to the Monitor ([11.4](#114-monitor-only-backends)).

### 1.3 The status bar

The footer on every page states the connection and offers the actions that change it:

| Text | Meaning | Buttons |
|---|---|---|
| `Disconnected` | no TRex session | **Connect…** |
| `Connecting…` | a connect is in flight | **Disconnect** |
| `Connected to <host>:<port> · TRex <version> · <n> ports · you own 0, 1` (`you own 12 of 16` above four ports) · `read-only` when the session is an observer | a live session | **Disconnect** |
| `Connection lost: <reason>` | the backend lost the server (it keeps the last port list) | **See Events**, **Reconnect**, **Connect…** |
| `Disconnected: <error>` | a connect or Reconnect failed | **See Events**, **Reconnect** (when the failed parameters are known), **Connect…** |
| `Backend offline, reconnecting…` | the browser lost the backend's WebSocket | **Connect…**, disabled |

**Reconnect** connects again with the lost session's parameters, without a dialog. A failed Disconnect or
Reconnect shows its message in red in the bar until the next attempt or the next change of state. Screens:
[02](gui-inventory/screens/02-control-disconnected.png), [05](gui-inventory/screens/05-connect-connecting.png),
[08](gui-inventory/screens/08-control-root-connected.png),
[31](gui-inventory/screens/31-control-connection-lost.png),
[32](gui-inventory/screens/32-statusbar-reconnect-failed.png),
[83](gui-inventory/screens/83-statusbar-backend-offline.png).

**Disconnect** always releases the ports you own. The **Release my ports when trex-web-gui exits** setting
([13.3](#133-session)) decides what a backend shutdown does.

### 1.4 Shortcuts and dialogs

- **Ctrl+Shift+C** opens the Connect dialog from any page (not on a `--monitor-only` backend).
- **Ctrl+Shift+P** goes to Settings.
- **Escape** closes the dialog or menu on top. Every dialog takes the focus and keeps Tab inside it, and gives
  the focus back to the control that opened it.
- A **ⓘ** button opens a note that floats under it, over the page, so nothing moves. Escape, a second click on
  the ⓘ, a click elsewhere, a scroll of the page or of what holds the ⓘ, resizing the window, or the ⓘ being
  hidden (another tab or page) closes the note.
- **Unsaved edits** stand out on every form with a **Save**: a field that Save would send has a teal dashed box
  and an `edited` tag after it, and `N unsaved changes` (`1 unsaved change`) shows beside Save, until Save lands
  or the field holds its stored value again (Cancel and Revert put it back). A field retyped to its stored value
  is not marked.
- Every tab strip, the devices tree, the setups and library lists and the context menus take the arrow keys,
  Home and End. The full list is in [16](#16-keyboard-shortcuts).

### 1.5 Themes

Settings → Appearance chooses **System**, **Light**, **Dark** or **High contrast**; the choice is kept in this
browser only and takes effect at once ([13.1](#131-appearance)). Dark and High contrast are shown on the
Dashboard in [59](gui-inventory/screens/59-dashboard-dark.png) and
[60](gui-inventory/screens/60-dashboard-high-contrast.png).

---

## 2. Connect to TRex

**How to open:** **Connect…** in the status bar, or Ctrl+Shift+C. The dialog opens over whatever page you are
on ([03](gui-inventory/screens/03-connect-dialog.png)).

**The form**

- **Saved**: a select of the saved connections, `—` for none. With one picked, **Delete** appears beside it and
  turns into **Confirm delete** on the first click. Picking one fills the fields below.
- **Host**: prefilled with the backend's `--server` host, else `127.0.0.1`.
- **Read-only (observer)**: connect without the right to change anything. It is a per-connect choice and is not
  saved with a connection. In a read-only session every port action, the Config, Traffic, Tools and capture
  controls are disabled with the tooltip `read-only session`, and the status bar says `read-only`.
- **Advanced** (a fold): **Command port** (4501), **Event port** (4500), **Timeout (s)** (3). While it is shut
  its summary line shows the values, `Advanced · ports 4501 / 4500, timeout 3 s`. It opens by itself when the
  prefilled or picked values are not the defaults, or when a validation message is about one of its fields
  ([04](gui-inventory/screens/04-connect-advanced.png)). The ⓘ beside Command port: the ports are set on the
  TRex server by `zmq_rpc_port` and `zmq_pub_port` in its `trex_cfg.yaml`; 4501 and 4500 when not set.
- Footer: **Save as…** at the left; **Cancel** and **Connect** at the right.

**Validation** (before anything is sent): `Host is required`; `Command port must be 1–65535`;
`Event port must be 1–65535`; `Timeout must be 0.5–60 s`.

**Connecting**: Connect reads **Connecting…**, Cancel is disabled, and the dialog stays open until the result
arrives ([05](gui-inventory/screens/05-connect-connecting.png)). It closes on success; on failure the backend's
message stays in the dialog ([06](gui-inventory/screens/06-connect-error.png)): the server is unreachable, it speaks
another API (`The server speaks STL API <v>; trex-web-gui supports only STL API 5.1 (TRex v3.08).`), or a session already
exists (`Already connected; disconnect first.`). Disconnect first if you want another server.

**A host that runs a stateful (-f) TRex**: a stateful run has no command port, so a connect to it fails. When
the GUI can tell (the daemon lists a `-f` process there, the Monitor is attached to it, or this GUI started the
run), the dialog says `<host> is running a stateful (-f) run. It has no command port; watch it on the Monitor.`
with an **Open Monitor** link ([106](gui-inventory/screens/106-connect-stateful-run.png)); the Control page
shows the same banner while disconnected.

**Save as…**: validates the form, then opens an inline **Name** row (the picked name or the host) with **Save**
and **Cancel** (Enter saves, Escape cancels the row). It writes host, ports and timeout to the backend's
settings file; a name already used (compared case-insensitively) is replaced
([07](gui-inventory/screens/07-connect-saved.png)).

**Settings file problems**: `The settings file could not be read, so defaults are shown. Saving is allowed: the
bad file is first kept as settings.toml.bad-<UTC>Z.`

---

## 3. Control page

Route `/control`. Heading, the toolbar, an alert line (only while there is an error), then two columns: the
devices tree at the left, the selected item's panel at the right.

Not connected, the right column says `Not connected. Use Connect… in the status bar (Ctrl+Shift+C) or click`
followed by a green **Connect…** button that opens the same dialog (after a failed Reconnect, `Not connected. Use
Reconnect or Connect… in the status bar (Ctrl+Shift+C) or click` **Connect…**); while the
session is lost, `Connection lost; Reconnect is in the status bar.` and the tree keeps the last state it saw
([02](gui-inventory/screens/02-control-disconnected.png),
[31](gui-inventory/screens/31-control-connection-lost.png)).

The alert line shows the result of the last failed command, and `Port N reported an error; see Events` when
the server reports a port error; a command that succeeds clears it.

### 3.1 The devices tree

One row for the server, `TRex-<hostname>`; one row per port, `Port N` or `Port N — <name>` with its state
badge (IDLE, STREAMS, TX, PAUSE, PCAP_TX, …), its owner at the right (`you`, another user's name, or `—`) and
an orange **SVC** badge when service mode is on or filtered; under a port, one row per profile,
`Profile <id> (<state>)`. A port with no saved name shows its confirmed kernel interface name in grey, as
`Port 0 ens1f0np0`, with the tooltip `Interface name found on the TRex host; Save it in the port's Name to keep
it`; the port's heading and the Dashboard's port columns read `Port 0 — ens1f0np0`. Only the default profile `_` can be controlled: another profile's row (one a
`trex-console` script added) selects the port's panel with every action greyed and labelled
`(read-only (multi-profile, P8))`, and the row itself says `read-only`.

Click selects. Right-click, Shift+F10 or the ContextMenu key selects and opens the context menu. Up, Down,
Home and End move; Enter selects. The root row is selected after a connect and after Start all
([08](gui-inventory/screens/08-control-root-connected.png), [12](gui-inventory/screens/12-control-profile-selected.png)).

**The root panel** (the server): a table **Version** (with mode and build date), **Host**, **CPU**, **Ports**;
then a ports table **Port**, **Driver**, **Description**, **MAC**, **NUMA**, **PCI**, **Interface**, **State**.
Interface is the kernel interface name when the backend could confirm it, or the `trex_cfg.yaml` entry marked
`(unconfirmed)` in grey; a note above the table says what the lookup could not do.

### 3.2 The toolbar

Two labelled groups and three single buttons. Every port action is disabled while the session is not
connected, in a read-only session, and when a non-default profile row is selected.

| Group | Button | Enabled when |
|---|---|---|
| **Port N** (`Port —`, greyed, at the root) | **Start** | you own the port and its default profile is in STREAMS. It starts the port with its Traffic form (multiplier, duration, force); the tooltip says what it will send, `Start at 5 kpps, no limit` or `Start at ×1, 30 s, force`. A form that cannot be sent opens the Traffic tab with the message (`Enter a multiplier greater than 0`; `Duration must be a positive number of seconds, or empty for no limit`). |
| | **Pause** / **Resume** | Pause while the default profile is TX; the button reads **Resume** while it is PAUSE. |
| | **Stop** | the port is TX, PCAP_TX or PAUSE, or its link is down while a profile on it is in one of those. |
| | **Acquire** / **Release** | Acquire when the port is not yours; Release when it is. |
| **All ports** | **Start all** | any owned port has its default profile in STREAMS. Opens the Start all dialog ([3.4](#34-start-all)). |
| | **Stop all** | any owned port is TX, PCAP_TX or PAUSE, or has its link down while a profile on it is in one of those; stops those. |
| | **Acquire all** | the server lists ports. |
| | **Release all** | you own a port. |
| | **Re-acquire my ports** | shown, green, only while another session of your user name holds a port (as when a trex-web-gui exited without releasing); its tooltip names the ports. |
| | **Clear stats** | the session is connected. Resets this browser's baselines for the Dashboard counters and the stream counters; nothing is sent to the server (its tooltip says so). |
| | **Setups ▾** | always: **Save current…** (needs a connected session) and **Manage setups…** ([5](#5-saved-test-setups)). |
| | **TRex daemon…** | always, disconnected included: opens the daemon dialog ([12](#12-trex-daemon-dialog)). |

Pause and Stop also cancel a pending live rate update ([4.3](#43-traffic-tab)). One request at a time: the
buttons are disabled while one is in flight.

Start, Stop and Pause buttons, here and on every page, are coloured by what they do: green starts or resumes,
red stops, amber pauses. A button filled with its colour can be used; grey means it cannot. Connect (the
status bar's and the dialog's) and Turn service mode on/off are green the same way. While the thing a
Start began is running, that Start is lit and names the state instead of the action: **● Transmitting** or
**❚❚ Paused** here (tooltip `Port N is transmitting` / `Port N is paused`), **● Running** for a capture
monitor, **● LLDP monitor running**, **● Wireshark open**, **● TRex running**. A lit button is disabled and keeps
its width, so the buttons beside it do not move. Buttons that do one thing once (Clear, Download pcap, Acquire,
Release, Clear stats) stay uncoloured.

### 3.3 The context menus

Right-click a tree row. Disabled items stay listed, greyed ([09](gui-inventory/screens/09-context-menu-root.png),
[10](gui-inventory/screens/10-context-menu-port.png)).

- **Root**: **Acquire all**, **Force acquire all** (takes the ports from other owners), **Release all**,
  **Re-acquire my ports** (force-acquires the ports the server lists as this OS user's that no handler of
  this session holds, as after a backend restart; enabled only while there is one), **Start all**, **Stop all**.
- **Port**: **Acquire**, **Force acquire**, **Release**, **Enable service mode** / **Disable service mode**
  (a filtered port can be switched off here, never set to filtered: that is the Control tab),
  **Assign profile…** (opens the Traffic tab with the profile field focused), **Start…** (the Traffic tab with
  the multiplier focused; it reads `Start (assign a profile first)` while the port is IDLE), **Stop**,
  **Pause**, **Resume**, **Update…** (the Traffic tab's multiplier, while the port transmits), **Reset** (red:
  the first click turns the item into `Click again to confirm`, and when captures exist on the server,
  `Capture <ids> will be removed from the server (<n> unsaved packets). Click again to confirm`; the
  confirmation lapses after 5 s), **Name…** (the Control tab with the Name field focused; this one works in a
  read-only session and on a port you do not own, because a name is the GUI's own file).
  After an acquire (also Force acquire, Re-acquire and Reset), another session's "port acquired" notice that the
  server sent before ours is shown in the log as `Port N was taken by '<who>' before our acquire; ignored` and
  does not change the port's owner.

Reset force-acquires the port, stops its traffic, removes its streams, clears its stats, turns promiscuous mode, RX
queueing and service mode off, and **removes every capture on the server**: the live monitor, unsaved recorders and
other clients' captures, whichever port you reset. The confirmation names the captures that will go. The port stays
yours afterwards.

### 3.4 Start all

Toolbar **Start all** or the root menu's. A dialog titled **Start all ports**: `Start ports 0, 1 at ×1 of each
profile's rate?`, the note `Each port's Traffic tab settings (rate, duration, Force) are not used.`,
**Cancel** and **Start all** (focused). It starts every owned port whose default profile is
in STREAMS, at ×1, with no duration ([11](gui-inventory/screens/11-start-all-dialog.png)).

---

## 4. A port's panel

Select a port or profile row. The heading is `Port N` or `Port N — <name>`; under it a tab strip **Control**,
**Config**, **Traffic**, **Counters**, **Tools**. Control is the first tab. The tabs stay loaded while another
is shown, so an inspected profile, typed values and a ping in progress survive a tab switch; the one exception
is the Traffic tab's live-update countdown, which is cancelled when the tab is hidden.

A request from the context menu or from Studio moves the panel to the right tab and focuses the field.

### 4.1 Control tab

Shown in [13](gui-inventory/screens/13-port-control-top.png), [14](gui-inventory/screens/14-port-control-details.png),
[15](gui-inventory/screens/15-port-control-bottom.png).

- **Status chips**: **Speed** (`10 Gbps` or `–`), **Link** (`up`, `down`, `–`), **Owner** (`(none)` when
  free), **State**, coloured like the tree's badge ([85](gui-inventory/screens/85-chips-idle.png),
  [86](gui-inventory/screens/86-chips-tx.png), [87](gui-inventory/screens/87-chips-pause.png)).
- **Port details (13)**, a fold: Driver, Description, MAC, PCI, Interface, Card, Bound driver, NUMA, RX filter
  mode, VLAN tags, Capture port, Grat ARP, RX queueing.
- **Name**: `Shown in the port lists. Saved by this GUI for <host>:<port>; clear both fields to remove the
  name.` **Name** (40 characters) and **Description** (500) with live counts, **Save** and **Revert**. A port
  with no saved name starts from its confirmed kernel interface name. The name is keyed by the port's MAC (by
  its number when the MAC is unusable or repeated), per server, and is stored in the backend's data
  directory, so every browser sees it. Over the limit: `The name is limited to 40 characters and the
  description to 500.` Disconnected: `Connect to name ports.` Unreadable file: `The port names file could not
  be read (<error>); it is kept as it is.`
- **Attributes**: **Promiscuous**, **Multicast**, **Link up**, **LED** checkboxes and a **Flow control** select
  (NONE, TX, RX, FULL). Each applies at once. A control is disabled with the reason as its tooltip:
  `not connected`, `read-only session`, `select the default profile`, `acquire the port first`, or
  `not supported by <driver>` when the driver reports no support (the test guest's af_packet supports none of
  Link up, LED and Flow control). Unticking Link up on a transmitting port first shows `Link down while
  transmitting can hang the TRex v3.08 server (upstream issue #1207). Stop traffic first, or click again to
  proceed.`; the second click within 5 s proceeds.
- **Service mode**: radios **Off**, **On**, **Filtered**. Off and On apply at once (a failure puts the radio
  back). Filtered shows the mask checkboxes **No TCP/UDP**, **BGP**, **DHCP**, **Transport**, **mDNS**, **All**
  and **Apply filter** (`choose at least one packet class` while none is ticked). The note says
  `Filtered mode needs the TRex server started with --software.`
- **Reset**: `Reset takes the port from any owner, stops traffic and removes all streams.` The red **Reset…**
  reads `Click again to confirm reset` after the first click (5 s), and lists the captures that would go. It
  is not gated by ownership, only by `not connected`, `read-only session` and `select the default profile`.

### 4.2 Config tab

The port's L2/L3 layer, VLANs and IPv6 ([16](gui-inventory/screens/16-port-config-top.png),
[17](gui-inventory/screens/17-port-config-l2.png)). Everything is disabled with one tooltip when the port
cannot take a configuration command, in this order: `not connected`, `read-only session`,
`select the default profile`, `acquire the port first`, `transmitting: stop traffic first` (the server allows
configuration only in IDLE, STREAMS or ASTF_LOADED), `busy: pinging` / `busy: scanning` / `busy: applying`,
`filtered service mode: set it on or off in the Control tab`. The reason also shows as a line beside Apply
(`Acquire the port first.`).

- **Mode and VLAN**: **Mode** radios **L2** / **L3**; **Outer VLAN**, **Inner VLAN** (1–4095); **Source MAC**
  (read-only, `set in the server config`); **Destination MAC**: an input in L2, and in L3 the ARP result,
  `resolved after Apply`, `unresolved`, or `unresolved: <reason>`.
- **Addresses**: in L3, **Source IPv4** and **Destination IPv4** with the ARP state beside it; **IPv6** radios
  **Off** / **Auto** / **Address** and **IPv6 source**. On a server with the legacy stack these are disabled
  and the note reads `IPv6 configuration needs the linux_based stack; this server runs legacy`.
- **Apply**, **Revert**, and `Unapplied changes` while the form differs from the server. Messages:
  `The outer VLAN must be a number from 1 to 4095.`, `Enter the outer VLAN before the inner one.`,
  `The destination MAC must look like 00:11:22:33:44:55.`, `Source and destination must both be IPv4.`,
  `The IPv6 source must be an IPv6 address.`; then the backend's answer, `Applied`, or
  `L3 set, ARP unresolved: <reason>`, or `Service mode could not be restored: <error>` (Apply puts the port in
  service mode for the ARP and puts it back afterwards).
- If the server's configuration changes while you are editing: `Changed on the server — Reload`. An untouched
  form follows the server by itself.

### 4.3 Traffic tab

Choose a profile, inspect it, assign it, start or re-rate the port
([21](gui-inventory/screens/21-port-traffic-empty.png), [23](gui-inventory/screens/23-port-traffic-inspected.png),
[24](gui-inventory/screens/24-port-traffic-transmitting.png), [25](gui-inventory/screens/25-port-traffic-update.png)).
Each port's form is kept while you switch ports, tabs and pages, and is emptied on a new connection.

**Profile**

- **Profile path**: a path on the backend host (nothing is uploaded). Enter inspects it. The ⓘ names the
  trex-web-gui host: `Paths are on <host>, the computer running trex-web-gui serve, not the one your browser is on.
  Nothing is uploaded.` and `.py profiles run as code on <host>.` Until the host name is known it reads `Paths
  are on the computer running trex-web-gui serve, …` and `.py profiles run as code there.`
- **Browse…**: the backend host's file browser ([4.3.1](#431-browse-dialog)). Picking a `.yaml`, `.yml` or
  `.json` file inspects it at once; a `.py` only fills the path, and you press Inspect.
- **Library…**: pick a profile from the Studio library ([4.3.2](#432-pick-from-the-library-dialog)).
- **Inspect**: loads the profile on the backend (a `.py` runs) and shows its tunables and its streams. A failed
  inspect keeps the form and shows the backend's message; a `.py`'s own error comes back as a block of text.
- After Inspect, a **tunables form** built from the profile's arguments (checkbox, choice, number or text per
  argument, with its help as tooltip) and **Extra arguments**. Re-inspecting the same path keeps your values.
- **New in Studio…** (an inline **Name** field, **Cancel**, **Create**) makes an empty library profile and
  opens it in Studio; **Copy to Studio** imports the inspected profile (with its tunables for a `.py`) into the
  library and opens it in Studio.

**What it holds**: a table **#**, **Name**, **Enabled**, **Mode**, **Rate**, **Packet**, **Length**, **VM** of
the inspected (or, after Assign, the assigned) streams, `Showing the first N of M streams` when cut;
**Show rate at ×1** (an owned port with streams) asks the server what ×1 means: `At ×1: max 1.1 kpps, L2 1.2
Mbps, 0.012% line utilisation`, and a fold **Full validate response** with the raw answer.

**Rate**: **Multiplier**, one box that reads the unit you type with the number. A bare number is a factor of
the profile's rates (`×` or `x` before or after it says the same); `%` is a share of the line rate, and a
slider shows beside the box; `pps`, `bps` (L2) and `bps L1` (or `bpsl1`) take an optional `k`, `M` or `G`.
Fractions are fine, a space before the unit is optional, and case does not matter (`m` is mega): `2`, `×0.5`,
`25%`, `10 kpps`, `2.5 Gbps`, `1gbps l1`. **A `k`, `M` or `G` needs a unit after it: type `200kpps`, not
`200k`**, which is refused (a bare `10 M` would otherwise be a factor of ten million). Beside the box, a reading
updated as you type: `= ×0.5 the profile's rates`, `= 25 % of line rate`, `= 10,000 pps`, `= 2.5 Gbit/s (L2)`;
an entry it cannot read shows `Can't read "<text>": a number, optionally with k/M/G and pps, bps or %, or × for
a factor.` instead (`Enter a rate above 0.`, `A percentage is at most 100.`). A multiplier loaded from a setup
or put back by Cancel is shown in its shortest form (`10000pps` reads `10 kpps`). The box is marked `edited`
while it differs from the multiplier last applied to the port.
**Duration (s)**, empty for no limit;
**Force** (start even if the server objects). The multiplier is disabled with the tooltip
`not connected`, `read-only session`, `acquire the port first` or `select the default profile`.

**Actions**

- **Assign**: replaces the port's streams with the inspected profile. Disabled until something is inspected
  (`Inspect a profile first.`). While the port is transmitting or paused it reads **Stop and assign** in red,
  and the first click turns it into `Click again to stop and assign` for 5 s.
- **Start**: as the toolbar's Start, lit the same way from the default profile's state.
- Beside the buttons, one line: the reason the tab is blocked (`Acquire the port first.`, as the tooltip
  says), else `Inspect a profile first.`, else what Start will send, `Start at 5 kpps, 30 s.` (the toolbar
  Start's tooltip, from the same form).
- **Live update**: while the default profile transmits and you may act, **a typed multiplier is applied on
  Enter or a click elsewhere in the page, never by switching to another app**. Typing alone sends nothing, so a
  half-typed `10` on the way to `10 Mpps` never reaches the port; if you switch apps mid-entry, come back and
  press Enter. Tab to another control and moving the slider count as a click elsewhere. Each of these starts a
  10 s countdown, `Applying <mult> in N s · Apply now · Cancel`; at zero the new rate is sent. An entry the box
  cannot read starts nothing. Cancel puts the last applied value back. Hiding the tab, pausing or stopping the
  port cancels it.
- A `.py` picked from the library carries the note `Assigns the .py source with these tunables; editor
  changes to this entry are not sent.`

#### 4.3.1 Browse dialog

Title **Browse for a profile** (or **Browse for a file to import**, **Browse for a capture**, **Pick a
stateful YAML** where other pages use it), over a dimmed page and above the dialog that opened it: **Up** and
the current directory, **Filter** (narrows the list by name as you type, with `N of M`), then a scrolling list
with one row per entry, directories first in bold with a trailing `/`, files with their size (`12.3 KiB`);
`Nothing here matches the filter.`, `The list was truncated; type the path instead.` and `No profiles or
directories here.` when they apply; **Cancel**. The frame stays put and only the list scrolls. A directory
enters it (and clears the filter); a file picks its full path and closes. The dialog opens at the last directory shown (kept in this browser), else at
the **Workspace directory** setting, else at the backend user's home ([22](gui-inventory/screens/22-browse-dialog.png)).

#### 4.3.2 Pick from the library dialog

One button per library profile with `<n> streams`. An entry with a load error, or a `.py` whose source file
is gone (`· source missing`), is disabled. A `.py` entry shows its source path and the note quoted above;
picking it fills the path and the entry's stored tunables without inspecting. Any other entry fills the path
with the library's JSON file and inspects it at once. `The library is empty. Add profiles in Studio.` when
there is none ([94](gui-inventory/screens/94-control-library-pick.png)).

### 4.4 Counters tab

The port's extended counters (xstats), polled every 0.5 s while this tab is shown, the page is visible and the
session is connected ([26](gui-inventory/screens/26-port-counters.png),
[27](gui-inventory/screens/27-port-counters-filtered.png)).

**Not empty** (on by default) hides zero counters; **Filter** matches a substring of the name;
**Reset counters** moves this browser's baseline (`Browser-only: moves this browser's baseline; the server's
counters are not touched`). `Counters are not updating` appears when a poll fails. The table: **Pin**, **Counter**,
**Value (since Reset counters)**; pinned counters stay at the top whatever the filter. The help line:
`Values are the server's absolute counters minus the baseline set by Reset counters in this browser. Counters
above 2^53 lose precision in the browser. A counter that goes backwards without a reconnect shows a negative
value until Reset counters.`

### 4.5 Tools tab

A side list, **Ping** and **IPv6 neighbours**, one tool shown. Both are disabled with the same reasons as the
Config tab ([4.2](#42-config-tab)). Both put the port in service mode while they run and put it back; a
failure to put it back reads `Service mode could not be restored: <error>`
([18](gui-inventory/screens/18-port-tools-ping-empty.png), [19](gui-inventory/screens/19-port-tools-ping.png),
[20](gui-inventory/screens/20-port-tools-neighbours.png)).

- **Ping**: **Destination** (IPv4 or IPv6), **Count** (default 5, 1–10), **Size** (default 64, 64–9216),
  **Ping**. Checks: `The destination must be an IPv4 or IPv6 address.`, `ARP resolution required: configure L3
  IPv4 first.` (an IPv4 ping needs the Config tab's L3 ARP resolved), `The count must be a number from 1 to
  10.`, `The size must be a number from 64 to 9216.` The replies of the newest run are listed as they
  arrive (they also go to the Events page under the kind **Ping**), then `N sent, N received`.
- **IPv6 neighbours**: **Scan** (reads `Scanning…`), **Clear**; a table **Type**, **MAC**, **IPv6** with
  **Set as L2 destination** (applies an L2 layer with that MAC and the port's VLANs; `L2 destination set to
  <mac>`), **Copy MAC**, **Copy IPv6** (`Copy failed: …` when the browser refuses the clipboard, which it
  does on a plain http origin other than localhost). `No IPv6 neighbours found.` after an empty scan. On the
  legacy stack: `TRex ports on the legacy stack do not answer IPv6; only other devices are listed.`

---

## 5. Saved test setups

A setup keeps the Traffic forms of one or more ports (profile, tunables, multiplier, duration, force), and
optionally each port's L2/L3 configuration and the Dashboard's layout, as a named file in the backend's data
directory. Setups are part of the settings export ([13.4](#134-back-up-and-restore)).

**Setups ▾** in the Control toolbar: **Save current…** (disabled unless connected) and **Manage setups…**
([64](gui-inventory/screens/64-setups-menu-disconnected.png), [65](gui-inventory/screens/65-setups-menu.png)).

### 5.1 Save current setup

**Name** (at least one letter or digit; up to 60 characters: `The name needs at least one letter or digit
(a-z, 0-9)`, `The name is limited to 60 characters`); **Server**, prefilled `host:port` (empty makes the setup
valid for any server); a **Ports** fieldset with a checkbox per port whose Traffic form has a profile
(`No port has a profile in its Traffic tab yet.` with none; a port whose multiplier or duration the server
would refuse is listed in red with the reason and `not saved`); **include port configuration
(L2/L3/VLAN/IPv6)** (a port without a complete layer shows `Port N: layer not included: no destination MAC` or
`no addresses`), **stop a transmitting port before assigning**, **include dashboard layout**; **Cancel**,
**Save**. A name whose slug is taken asks `A setup with this name already exists (<slug>). Replace it?` and
the button becomes **Overwrite** ([67](gui-inventory/screens/67-save-setup-no-profile.png) to
[70](gui-inventory/screens/70-save-setup-overwrite.png)).

### 5.2 Test setups dialog

**Manage setups…** works disconnected too. Left, the list: name, `<server or any server> · N ports`, and
`· loaded` (`, N edits` with edits since Load) for the loaded one; a file that could not be read is a disabled
red entry. Right, the chosen setup: **Rename**, **Duplicate**, **Delete** (`Delete setup <name>?` with
**Confirm delete** / **Cancel**; rename and duplicate open a **New name** row), `Saved for <server> · updated
<time>`, the ports table **Port**, **Profile**, **Rate**, **On Run** (`force-acquire`, `stops a transmitting
port first`, `applies a port configuration`, or `—`), **Status**; then **Load**, **Run** and the result, and
**Close** ([66](gui-inventory/screens/66-manage-setups-empty.png),
[71](gui-inventory/screens/71-manage-setups-list.png) to [82](gui-inventory/screens/82-manage-setups-540px.png)).

- **Load** (needs a session) inspects every port's profile (a `.py` runs on the backend host) and fills the
  Traffic forms. Each port's status becomes `ready` or its error. A setup saved for another server loads with
  a red mismatch line. The empty state: `No saved setups. Use Setups ▾ → Save current… on the Control page.`;
  disconnected, Load and Run are greyed with `Connect first`.
- **Run** applies the loaded setup in phases, acquire, stop (the ports marked to stop first), layer, assign,
  start, and stops at the first failure without rolling back. The status column moves through `acquired`,
  `stopped`, `layer applied`, `profile assigned`, `started`, or `failed at <step>` (the reason is its tooltip); the result line
  reads `Run finished: started ports 0, 1.` or `Run stopped at port N: failed at <step>: <message>.`
  Afterwards **Stop started ports** stops what did start. A port owned by someone else offers
  **Force-acquire port N and continue**, then `Take port N from its current owner?` with **Force-acquire**.
  Run is unavailable with `read-only session`, `Connect first`, `a Run is in progress`, `busy`, and until a
  Load of this setup succeeded (`Load first: Run uses the profiles a Load inspected.`); a setup edited since
  its Load says so.
- Edits made in the Traffic tabs after a Load are listed (`Edited in the Traffic tab since Load. Run uses the
  setup as saved; Save and Run saves these edits first.`); Run reads **Run as saved** and **Save and Run**
  saves the forms into the setup, loads it again and runs it (`Save and Run is unavailable: fix the form
  first: Port N: <problem>` when a form cannot be saved).
- A Run carries on if the dialog is closed; reopening it shows the progress and the result.

---

## 6. Dashboard

Route `/dashboard`. A tab strip **Overview**, **Streams**, **Latency**, **Utilization**, with **Clear stats**
at its right end (the same action as the toolbar's). All four panels stay loaded, so charts keep their history.
Each tab judges its own feed: when no message has arrived for three times the stats interval, an orange
`<Statistics | Stream stats | Latency stats | Utilization stats> are not updating (last update HH:MM:SS)` banner
(the time is left out before any message has arrived) tops the panel and
the panel is greyed; while the session is lost, `Connection lost: values are the last received` shows under
the heading ([34](gui-inventory/screens/34-dash-disconnected.png), [41](gui-inventory/screens/41-dash-stale.png),
[42](gui-inventory/screens/42-dash-connection-lost.png)).

Every chart's time axis is one line: `:15` between whole minutes, the time (`3:46pm`) at one, and no date. Every
chart has its own legend under it, with no standing time entry (hovering adds the time). A chart of one series reads `latest <value>`;
while the cursor is over the plot it reads the cursor's time and value instead. A chart of several series has
one chip per series: ✓ or ✗, the series' colour, its label and its latest (or hovered) value; a click hides or
shows that line, and the last chip still shown is disabled (`The last shown series cannot be hidden`). A
**Y axis** control, **Fit** (the default: the range fits the lines) or **From 0** (the axis starts at 0), sits
beside Layout on Overview and beside the charts on Streams, Latency and Utilization; it is one setting for
every Dashboard chart, kept in the browser with the interval and layout (a saved setup keeps the
interval and layout, not this).

### 6.1 Overview

[35](gui-inventory/screens/35-dash-overview-top.png), [36](gui-inventory/screens/36-dash-overview-4charts.png).

- **Tiles**: **Tx** (Tx L2, Tx L1, PPS, Queue full), **Rx** (Rx L2, Drop rate (Tx − Rx)), **System** (CPU, Rx CPU,
  Active ports, Streams). Drop rate is TRex's: what all ports send minus what they receive (0 below 10 % of Tx), so
  traffic that does not come back to a TRex port counts as dropped; its tooltip says so. A CPU tile turns red at 85 %. While any port is in service mode, an orange bar under the
  page heading, on every Dashboard tab, reads `Service mode is on for port N.` (in bold). For the ports this
  session owns it adds a green **Turn service mode off for port N** button; it never acquires a port, so for any
  other port (or read-only) it reads `Turn it off on the Control page: acquire the port, then its Control tab,
  Service mode Off.`
  `Connection lost: values are the last received` uses the same bar.
- **Ports table**: **Show** (All / My ports) and **▸ Show bytes** (it reads **▾ Hide bytes** while the bytes
  rows show); a column per port and **Total**; rows Tx/Rx L1 bps, L2 bps, pps with ▲/▼ trend marks, then
  `Counters: since connect, Reset or Clear`: packets, bytes (folded behind Show bytes), errors. A mark is the TUI's trend: the weighted change over the last 5 samples, one
  arrow above 1 %, two above 2 %, three above 5 %, none below 1 % or while the rate is under 10; its tooltip says so.
- **Charts**: **Interval** (60, 90, 120, 300 s) and **Layout** (1, 2 or 4 charts); each chart's caption is its
  series select (Tx pps, Rx pps, Tx bps L2, Rx bps L2, Tx bps L1, Rx bps L1) over the Total. Defaults: 60 s,
  2 charts, Tx pps and Rx pps.

### 6.2 Streams

Per-stream (PG ID) flow statistics; needs streams with flow stats or latency ([37](gui-inventory/screens/37-dash-streams.png)).
A table **Chart** (checkbox), **PG ID** (▸/▾ expands the per-port rows), **Type** (`flow` or `latency`), Tx/Rx
pps, Tx/Rx bps L1 and L2, Tx/Rx pkts and bytes (`since the stream was added, or Clear`), **Loss**, **Loss/s**;
a **Page** select above 100 ids; `Showing the first 1151 of N streams` when the backend caps the ids it reads;
`rx_err` / `tx_err` per port below. The chart **Streams: <metric>** with **Metric** (Tx pps, Rx pps, Tx bps L1,
Tx bps L2, Rx bps L2, Loss/s) and **Interval**; the first four ids are charted by themselves, and the checkbox
is disabled with `At most 8 streams can be charted; untick one first` once every colour is used.
`No streams with a PG ID are active.` when there are none, followed by how a stream gets one (Studio >
Properties: RX stats Flow stats or Latency, with a PG ID; then assign and start) and what it adds.

### 6.3 Latency

[38](gui-inventory/screens/38-dash-latency-top.png), [39](gui-inventory/screens/39-dash-latency-bottom.png).
A table **Chart**, **PG ID**, **Max (µs)** (the largest last-max seen since connect or Clear), **Server max
(µs)** (the server's, which Clear does not reset), **Avg (µs)**, **Last (µs)** to **Last-9 (µs)**, **Jitter
(µs)**, **Errors** (seq too low plus seq too high); a **Latency histogram** of one **PG ID** with **Bars** (11,
17 or All) and the counters Dropped, Duplicates, Out of order, Seq too high, Seq too low; the chart
**Latency: <metric> (µs)** (Average, Max, Last max, Jitter).

### 6.4 Utilization

[40](gui-inventory/screens/40-dash-utilization.png). A CPU table **Core**, **Ports**, **Avg (%)** (mean of the
newest 5 samples), **Latest (%)**; **CPU utilization over time** over 300 s (the first 8 cores; `Charting the
first 8 of N cores.`); an mbuf table with a column per buffer size and **RAM (MB)**: Total, then per socket
used and percent. `No utilization data yet.` before the first sample.

---

## 7. Events

Route `/events`. Two views, **Events** and **RPC** ([28](gui-inventory/screens/28-events-page.png) to
[30](gui-inventory/screens/30-events-rpc.png), [33](gui-inventory/screens/33-events-marks.png)).

**Events**: filter chips **All**, **Errors**, **Warnings**, **Server events**, **Info**, **Ping**, each with its
count (several can be pressed); a **Filter text** field; **Copy** and **Save log…** (a `.txt` download named
`trex-web-gui-events-<UTC stamp>.txt`) take what the filters show; **Pop out ↗** opens a second window that shows
the same log, read-only (hidden in a browser without BroadcastChannel); **Clear** (red) empties the log. The
status line: `N entries · kept across reconnects · Following new entries`, or **Jump to newest** once you
scroll up. The table **Time** (headed with the browser's zone, `Time (EDT, UTC−04:00)`; each row the date in
grey, then the time), **Kind**, **Message**; a row between dashed lines, kind `session`, marks each connect,
disconnect and lost connection and is never filtered out. Copy and Save log… write one line per entry,
`[INFO] 2026-10-27T16:48:26-04:00 <message>` (`--- <stamp> <message> ---` for a session row): the legacy
`HH:mm:ss` with the date and UTC offset added. The log holds 2000 entries (older ones drop) and lives in this
browser: a reload empties it.

**RPC**: the raw JSON-RPC between the backend and TRex. **Capture** (a session-wide switch, kept across page
reloads; `Capture is off.` / `Waiting for RPC traffic…`), **Include polling** (off by default, so stats, port
status and event polls do not push your own commands out of the 500-entry buffer), **Filter text**, **Copy**;
one line per message, `→` a request, `←` a response; a body over 8 KB is cut on screen (`Copy copies the full
body`).

---

## 8. Capture

Route `/capture`. The heading's ⓘ: `The monitor records the packets of the ports you choose; a recorder keeps
them on the server until saved. Other capture tools, such as tcpdump on the host, may not see a port TRex is
driving.` A tab strip **Live**, **Recorders (n)**, **Saved files (n)**, **LLDP**. The page keeps its forms and
rows while you are elsewhere; a hidden tab polls nothing ([43](gui-inventory/screens/43-capture-disconnected.png)).

Both the monitor and a recorder are TRex captures on the server. The server holds 10 captures in all; the GUI
starts none above 9, and keeps one for ping, ARP and scan. The Rx ports of a capture must be in service mode.

### 8.1 Live

[44](gui-inventory/screens/44-capture-live-setup.png), [45](gui-inventory/screens/45-capture-live-running.png).

- **Tx ports** and **Rx ports** checkboxes (the Tx ⓘ: `On the af_packet test guest a Tx capture recorded
  nothing unless that port was in service mode; other NICs are unverified.`), **BPF filter**, **Limit**
  (default 100, 1–100000; above 10000 an ⓘ says `Large captures use about 9 KB of server memory per
  packet`). The inputs are locked while a monitor runs.
- **Start** is disabled with the reason as tooltip and as a line under the bar: `not connected`,
  `read-only session`, `a monitor is already running`, `Rx port(s) N are not in service mode`,
  `choose at least one port`, `the limit is 1 to 100000`. **Turn service mode on for ports N** appears when
  that is the block.
- **Stop**, **Clear** (empties the browser's rows), **Download pcap** (the rows, written by the browser as
  `monitor-<UTC stamp>.pcap`), **Open in Wireshark** and **Stop Wireshark** ([8.6](#86-wireshark)). While the
  monitor runs, Start is lit as **● Running**.
- Counters: **Fetched**, **Matched**, **Server dropped**, **Browser dropped**; `N packets not shown` when the
  browser holds fewer than were fetched. `Capture N was removed on the server (<how>).` when the server lost
  it.
- The packet table **No.**, **Time**, **Port**, **Mode**, **Src**, **Dst**, **Type**, **Len**, **Info**, 100
  rows per page with a **Page** select (the newest page is followed until you pick another). Up and Down move
  the selection; Enter or a double-click opens the packet viewer. Empty, it reads `No packets. Start the monitor
  to capture.`, or `No packets yet: waiting for the first one that matches.` while the monitor runs.

### 8.2 Packet viewer

**Packet N**, a summary line, then the layer tree at the left (each field with its value and byte range;
click a field to mark its bytes) and the hex and ASCII dump at the right (click a byte to select the field
that holds it); **Expand all** / **Collapse all**; **Copy hex**; **Close**. `Packet tree unavailable: <error>`
leaves the bytes shown ([46](gui-inventory/screens/46-packet-viewer.png)).

### 8.3 Recorders

[47](gui-inventory/screens/47-add-recorder-dialog.png), [48](gui-inventory/screens/48-capture-recorders.png).
**Add recorder** and an ⓘ with the slot limit and the NIC caveat (on some NICs stopping a capture turns the
NIC's capture path off for port pairs no running capture still receives on; read from the server source, not
measured). `No captures on the server.` or a table of every capture on the server, the monitor's own included:
**ID**, **Origin** (`this GUI`, `TRex service`, `other`; `(monitor)`), **State**, **Mode**, **Tx**, **Rx**,
**BPF**, **Packets** (`count/limit`), **Fetched**, **Bytes**, and the actions **Stop** (an active recorder),
**Save** (fetches the packets into a pcap file in the backend's capture directory; progress `fetched N of M`,
then `Saved as <name>`; **Resume** when a partial file exists or a save failed), **Remove** (`<n> packets will
be lost. Remove? Click again to confirm` when unsaved packets would go). The monitor's row has no actions; a
TRex service capture (ping, ARP, scan) shows a disabled Remove.

**Add recorder**: **Tx ports**, **Rx ports**, **BPF filter**, **Limit** (default 1000), the same blocks as
Live plus `9 of 10 server slots used; one is kept for ping, ARP and scan`; **Turn service mode on for ports
N**; **Cancel**, **OK**.

### 8.4 Saved files

`Saved to <dir> on the backend host` (`captures/` under the data directory), the count, and a table
**Name** (a `partial` badge for an unfinished save), **Size**, **Saved**, **Packets**, **Download**, **Delete**
(`Click again to confirm`) ([49](gui-inventory/screens/49-capture-saved-files.png)).

### 8.5 LLDP

The LLDP view is the monitor with a preset filter, `ether proto 0x88cc or (vlan and ether proto 0x88cc)`:
**LLDP Rx ports**, **Start LLDP monitor** (blocked by `Stop the current monitor first` while another monitor
runs, and the Live tab's reasons; lit as **● LLDP monitor running** while this one runs), **Stop LLDP monitor**
beside it (disabled with `the LLDP monitor is not running` until then), **Turn service mode on for ports N**
when a chosen Rx port is off, and, while no monitor runs, a green **Turn service mode off for port N** for the
chosen Rx ports in service mode that this session owns (it never acquires one). A line gives the Rx ports'
service mode, `Service mode: on for port 0; off for port 1.`, and while the monitor runs adds `Stopping leaves
service mode on; Turn service mode off appears here once stopped.` The
note: `Some NICs consume LLDP in firmware (Intel X710/XL710 under i40e is the known case), so a neighbour that
sends LLDP may never appear here. Unverified on hardware: the test guest has no LLDP speaker.` Then `Start the
LLDP monitor to see neighbours.`, `No LLDP neighbours seen by the current monitor.`, or a table **Port**,
**VLAN**, **System name** (the system description in its tooltip), **Port ID**, **Port description**, **Chassis
ID**, **Capabilities**, **Management** (one address per line), **TTL (s)**, polled every 2 s while the tab is
shown; an entry drops when its TTL runs out ([50](gui-inventory/screens/50-capture-lldp.png)). An ID's LLDP subtype
follows it, as in `et-0/0/2 (interface name)`, only when the value does not show it: a MAC or a network address
has none. Every ID's subtype is in its tooltip (`Port ID subtype: MAC address`).

### 8.6 Wireshark

**Open in Wireshark** starts Wireshark on the backend host (the **Wireshark path** setting) on the running
monitor's packets and is lit as **● Wireshark open** while it runs; **Stop Wireshark**, beside it, ends it and
is disabled until then. Open in Wireshark is disabled with `checking whether Wireshark can be
launched`, the preflight's reason (`Wireshark: <reason>` under the bar, for a missing binary or display),
`not connected` or `start the monitor first`. Nothing is shown in a screenshot.

---

## 9. Studio

Route `/studio`. The profile library: TRex profiles kept as files on the backend host, edited offline (no
session needed), simulated, exported, and assigned from Control. A link **Packet editor** at the top goes to
[10](#10-packet-editor). Layout: the library list at the left with its actions under it; the selected
profile's editor at the right ([88](gui-inventory/screens/88-studio-library.png)).

### 9.1 The library

The directory is `<data dir>/profiles` or the **Profile library directory** setting. Each profile is
`<slug>.json` (exactly what the TRex client writes) plus `<slug>.gui.json` (its name, source and per-stream
editor documents). The list shows each profile's name and `<n> streams · <source kind>` (`py`, `yaml`, `json`,
`pcap`, `new`, `no source`), then `source missing` or its load error; files in the directory that are not
profiles are listed in red, not selectable. `No profiles yet. New… or Import… to add one.`

- **New…**: an inline **Name** with **Cancel** and **Create**; makes an empty profile and selects it.
- **Import…**: the import dialog ([9.2](#92-import-dialog)).
- For the selected profile: **Rename…** (the New name row says `N test setups point at this profile; they
  will need the new path.` when saved setups use it), **Duplicate…**, **Delete** (`Delete profile <name>?`,
  **Confirm delete**), **Use in Control…**: a dialog **Use in Control** with a **Port** select, `Fills the
  profile field of that port's Traffic tab; nothing is assigned until you press Assign there.`, and the rate
  that port's form keeps, `Port 0's rate stays 5 kpps: check it on the Traffic tab before you press Assign.`
  (the handoff does not change it); **Open Traffic tab** goes to Control with the Traffic tab open
  (`No ports: connect to a TRex server in Control first.` without a session).
- While the embedded packet editor holds unsaved edits, Rename and Delete are off: `The packet has unsaved
  edits: save or cancel them first.`, and choosing another profile asks `Discard the packet edits?`
  (**Discard** / **Keep editing**).

### 9.2 Import dialog

**Import a profile** from the library, or **Import streams from pcap** from the editor
([92](gui-inventory/screens/92-studio-import-pcap.png)).

- **Source**: **On the backend host** (**Path** with **Browse…**) or **From this computer** (a **File**
  chooser; the file is uploaded to the backend, at most 64 MiB, 8 uploads kept for an hour).
- **Kind** from the suffix: `.py` (backend host only: `A .py profile must be on the backend host: the library
  keeps a link to it.`), `.yaml`/`.yml`, `.json`, `.pcap`/`.cap`; otherwise `none: only .py .yaml .yml .json
  .pcap .cap files import`. Appending accepts pcaps only: `Only a .pcap or .cap file appends streams.`
- A `.py` adds **Tunables** with **Inspect** (`Inspect runs the profile to read its tunables.`), then the
  tunables form; the entry keeps the source path, and Control assigns that `.py` with those tunables.
- A pcap adds **Pcap options**: **Name prefix**; **Timing**: `From the pcap, sped up ×` N or `Fixed gap`
  (µs); **Loops**; **Rewrite source IPv4** and **Rewrite destination IPv4**, each a start address and
  **Count**. The ⓘ: every packet becomes one stream, chained in file order, the last looping to the first;
  the rewrite needs IPv4 packets and refuses an import that would rewrite one without IPv4.
- **Name** (defaults to the file's stem); **Cancel**, **Import**. While the editor is saving a table change:
  `Waiting for the table's last change to be saved and read back.`

### 9.3 The profile editor

[89](gui-inventory/screens/89-studio-stream-selected.png). An information line, `Source: <kind> <path> · saved
<UTC time> · N streams`; for a `.py`, `Control assigns the .py source with its tunables; edits here are not
sent.`; `Imported for port 0; port 1's streams differ.` for a profile whose ports differ.

**Profile actions**: **Add stream** (a continuous 1 pps template stream at the end), **Import stream from
pcap…**, **Export ▾** (**JSON**, **YAML**, **Python**; for a `.py` entry, `Python (from the frozen JSON, not the
.py source)`), **Simulate** ([9.7](#97-simulate)), and for a `.py` entry **View source…** (the source path and
the file's text, read-only; `The source file is missing: it was moved or deleted on the backend host.`)
([114](gui-inventory/screens/114-studio-py-source.png)).

Every change is saved at once; there is no Save or Revert for the profile. When another client changed the
file: `This profile changed elsewhere. Reload`; when it was renamed or deleted: `This profile was renamed or
deleted elsewhere. Reload library`.

### 9.4 The stream table

**Undo**, **Redo**, **Up**, **Down**, **Duplicate**, **Copy**, **Paste**, **Delete** (`Delete N streams?` with
**Confirm delete** / **Cancel**); the same from the keyboard while the table has focus: Ctrl+Z, Ctrl+Y or
Ctrl+Shift+Z, Alt+↑/↓, Ctrl+C, Ctrl+V, Delete, F2 (rename). Columns **Enabled** (a checkbox, saved at once),
**#**, **Name** (double-click or F2 renames inline; every `next` that named the stream follows), **Packet**,
**Length (with FCS)**, **Mode**, **Rate**, **Next**. Click, Ctrl+click and Shift+click select. 100 rows per
page: **Previous**, `rows a–b of N`, **Next**. A write that moves indices locks the table until the page has
been read back. Notices: `Copied N streams.`, `Pasted N streams`, `Nothing to paste: copy streams first.`,
`<names> now stop instead of going to the deleted stream.`, `Undid <action>.`; a warning under the bar when an
enabled stream's Next is disabled: `<stream> goes to <next>, which is disabled: TRex refuses this profile at
assign until <stream>'s Next changes.`

The table is framed; a row lights up under the pointer and ends in `›`, the open stream's row has an accent bar
on its left, and the other selected rows keep their tint and outline. Until one stream is open, a dashed panel
stands where its properties will be: `Click a stream to edit its properties and packet.` and `⌘-click or
Shift-click selects several, for Copy, Duplicate and Delete.` (Ctrl-click off a Mac); with several selected,
`N streams selected. Click one to edit it.` With one, the tabs **Properties** and **Packet**.

### 9.5 Properties tab

[90](gui-inventory/screens/90-studio-properties.png). **Mode** (Continuous; Single burst with **Packets**;
Multi-burst with **Packets per burst**, **Bursts**, **IBG (µs)**), **Rate** (one box, below), **Enabled**, **Self
start**, **Start paused** (ⓘ: experimental in TRex), **Next** (Stop, or a named stream; disabled for a continuous
stream), **Action count**, **ISG (µs)**, **Random seed** (`0 = none`), **Core** (`-1 = any`), **Source MAC from
packet** (ⓘ on the `flags` bits), **Destination MAC** (From config file, From packet, By ARP), **Dummy stream**,
**RX stats** (None, Flow stats, Latency, with **PG ID**; a TPG rule is shown read-only), **Extra stats** (VXLAN
and IEEE 1588 as imported, read-only: the TRex client drops both), **Cache** (Off or N packets, with **Cache
packets**). A field's error is shown beside it.

**Rate** is one box that reads the unit you type with the number: `pps`, `bps` (L2), `bps L1` (or `bpsl1`) with
an optional `k`, `M` or `G`, or `%` of the line rate. **A bare number is pps.** Fractions are fine, a space
before the unit is optional, and case does not matter (`m` is mega): `1000`, `1.5 Mpps`, `10 Gbps L1`, `25%`.
**A `k`, `M` or `G` needs a unit after it: type `200kpps`, not `200k`**, which is refused. Beside the box, a
reading updated as you type: `= 1,500,000 pps`, `= 10 Gbit/s (L1)`, `= 25 % of line rate`; an entry it cannot
read shows `Can't read "<text>": a number, optionally with k/M/G and pps, bps or %.` instead. A stored rate is
shown in its shortest form (1500000 pps reads `1.5 Mpps`), and saving it untouched sends nothing.

**Previous** and **Next** move between streams (with unsaved edits: `Discard changes?`, **Discard** / **Keep
editing**); **Cancel** puts the stored values back; **Save** sends only the changed fields and is disabled
until there is a change and while a field is invalid; each field it would send is marked `edited`, and
`N unsaved changes` shows beside it ([1.4](#14-shortcuts-and-dialogs)). The name is not here: rename in the
table.

### 9.6 Packet tab

[91](gui-inventory/screens/91-studio-packet.png). The packet summary, `N bytes (N+4 with FCS)`, `Field
engine: N instructions`; **View packet…** (the packet viewer, [8.2](#82-packet-viewer)), **Edit packet**, and
**Export ▾** (**pcap**, written by the browser from the stored bytes; **YAML**, from the backend).

**Edit packet** opens the embedded packet editor ([10.2](#102-the-editor) onwards) with **Presets…**, **Cancel**
and **Save** above it, and an `edited` tag beside Save while the packet differs from the one opened
([113](gui-inventory/screens/113-studio-embedded-packet-editor.png)).
Save builds the packet on the backend and writes the stream's packet, field engine and editor document;
`Saved.`, or `The packet changed while it was being built: nothing was saved. Save again.`, or `The stream
changed since the editor was opened (another change moved or replaced it): Cancel, then open it again.`
Cancel with unsaved edits asks `Discard the packet edits?` Editor-moving table actions are off meanwhile.

### 9.7 Simulate

An offline check with no server ([93](gui-inventory/screens/93-studio-simulate.png)): `Round trip: identical`
or `differs` with the differences (the profile through the TRex client and back); a table **#**, **Name**,
**Length**, **Mode**, **Rate as given**, **pps**, **bps L1**, **bps L2**, **Packets** (the first 100 streams)
and a **Total** row; a value that cannot be known offline (a percentage of the line rate) is a dash, with
`Streams given as a percentage add N % of the line rate, which is not known offline, so the totals leave them
out.`; **Chains**, each `next` chain with how it ends and its packet total; `Nothing starts <streams>.`; the
notes; **Close**.

---

## 10. Packet editor

Route `/studio/packet`, from the **Packet editor** link on Studio. A standalone editor for one packet, kept as
a `.trp` file in your downloads, as a template on the backend, or added to a library profile as a stream. The
same editor is embedded in Studio's Packet tab ([9.6](#96-packet-tab)). The page stays loaded while you are
elsewhere ([107](gui-inventory/screens/107-packet-standalone-template.png)).

### 10.1 The page

The heading `Packet editor — <name or Untitled>` with a `•` while there are unsaved changes. **File ▾**:
**New**; **Open .trp…** (a file from this computer; the note: `A .trp may contain expressions; they are
evaluated on the backend host when the packet is built.`; at most 4 MiB); **Save** (the note
`Downloads a .trp file.`; downloads `<name>.trp`); **Save as template…**; **Open pcap…**; **Save as pcap** (one packet, written by the browser
from the bytes the backend builds); **Add to profile as a new stream…**. Then **Templates…** and
**Presets…**. Replacing a packet that has unsaved changes asks `Discard changes?` (**Discard** / **Cancel**).
`The packet changed while the other one was being opened: the opened one was dropped.` when a pick lands on a
packet you have since changed.

- **Templates** ([10.5](#105-templates)); **Presets** ([10.6](#106-presets)); **Open pcap** ([10.7](#107-open-a-packet-from-a-pcap));
  **Add to profile** ([10.8](#108-add-to-profile)).

### 10.2 The editor

**Undo**, **Redo**, **Expand all**, **Collapse all**; the tabs **Packet** and **Field engine**. The Packet tab:
a breadcrumb `Ether > IP > UDP · N bytes` (click a layer to open its pane); one collapsible pane per layer
(`<id> as <real id>` when the bytes decode as something else; a `!` for an invalid structure); **Add layer…**
(a text box that lists the protocols the last layer can carry first, then every protocol; `unknown protocol
"x"`); the hex view. The packet is rebuilt on the backend after every edit; the hex view and byte ranges show
the last good build, and a failed build marks the field and shows the message.

**A layer's menu** (the ⋮ button or a right-click on its title): **Move up**, **Move down**, **Delete** (the
first layer is fixed). Ctrl+D deletes the layer you opened last.

### 10.3 Fields

Each row: the field's name, a control for its type, a `•` when you set it (with a `×` to reset it to the
default), a `!` and the message when the build refused it, and its byte range (a button that marks the bytes;
`not in packet` for an ignored field, `bits` for a sub-byte one).

- Numbers take decimal or `0x` hex; an ENUM takes a name from its list or a number; a value outside the
  definition's range is refused (`must be from 0 to 255`). Enter commits, Escape reverts the text.
- **Right-click a field**: **Generate** (a random value), **Set to default**, **Edit as expression** / **Edit
  as text** (a Python expression, `evaluated as Python on the backend`), and **Field engine: <template>** for
  each field-engine template the backend offers (adds its instructions on this field and opens the Field
  engine tab).
- A BITMASK field shows its flags as checkboxes and choices; IP and TCP options are read-only.
- A BYTES field (a payload) shows its bytes cut at 48 characters and a **Payload…** button
  ([10.4](#104-payload-dialog)).

**The hex view**: 16 bytes a row, offset, hex, ASCII. Arrow keys, Home and End move between bytes; Enter or
Space, or a click, selects the field holding the byte. Typing two hex digits on a byte replaces it (the
backend reconstructs the field; `The packet is not built yet: try the byte edit again when it is.`); Escape
drops a half-typed byte.

### 10.4 Payload dialog

[110](gui-inventory/screens/110-packet-payload-editor.png). **Source** chooses how the bytes are given: text,
text repeated to a size, a file, a file repeated to a size, hex bytes repeated, random printable characters,
random bytes. Then its fields: **Text** or **Hex bytes**; **File** (at most 1 MiB); **Size is** `payload
length` or `packet size (with FCS)` with **Size** (1 to 1 MiB) for the sized sources; **Seed** for the random
ones (`not 0: the service reads 0 as <default>`). **Cancel**, **Save**.

### 10.5 Templates

**Server templates** (the stock ones, as a tree by `/`; one that cannot be built is disabled with its reason)
and **Your templates** (yours, in the **Templates directory** setting, else `<data dir>/templates`; each with
**Delete**, `Delete <name>?`); click a template to open it. From the standalone page the dialog also saves:
`Save the packet as a template` with a name like `lab/syn-flood`, **Save**; an existing name asks `Replace
<name>?`. **Close**.

### 10.6 Presets

[108](gui-inventory/screens/108-packet-presets.png). Four forms, each ending in **Open in editor**:
**Flood** (one UDP or TCP packet; protocol, source and destination MAC from the TRex configuration, from the
packet, or stepped up/down with a count and step, IPs, ports, frame size fixed/stepped/random, payload text),
**IP sweep** (source, destination or both IPv4 addresses stepped through a range by count or last address;
checksum in software or hardware), **Frame-size sweep** (UDP packets between two sizes, stepped or random),
**VLAN** (a UDP packet behind one or two 802.1Q tags with VLAN and priority). A bad number says so beside the
button (`Source port must be a whole number`). A preset's MAC mode is carried into the stream when the packet
is added to a profile.

### 10.7 Open a packet from a pcap

[111](gui-inventory/screens/111-packet-pcap-picker.png). **On the backend host** (a path with **Browse…**) or
**From this computer** (a `.pcap`/`.cap` upload); **List packets**; the capture's warnings (cut or truncated
records); `Packets 1–100 of N` with **Previous** / **Next**; a row per packet with its number (a button that
opens it), bytes and summary. A capture over the **Largest capture the packet editor opens (MiB)** setting is
refused before it is read: `<path> is N bytes; the pcap size cap is 64 MiB (Settings, pcap_size_cap_mib)`.

### 10.8 Add to profile

[112](gui-inventory/screens/112-packet-add-to-profile.png). **Profile** (a select of the library; an entry
with a load error reads `cannot be changed`; a `.py` entry carries the note that editor changes are not sent;
its last choice, **New profile…**, shows a **New profile name** box), **Add** (**Create and add** for a new
profile), **Cancel**. An empty library shows only the name box, after `The library has no profiles yet. Name one
and the packet becomes its first stream.`, so the packet never has to be left to make a profile. A created
profile joins the list and stays chosen, so a failed add is retried into it. The packet becomes a new last
stream (continuous, 1 pps, unnamed) with its field engine
and editor document; `Added as stream N of <name>.` with **Open <name> in Studio**. `The packet document is too
large to store with the stream: it will reopen from its bytes.` above the sidecar's size limit.

### 10.9 Field engine tab

[109](gui-inventory/screens/109-packet-field-engine.png). **Add instruction** (a text box listing the
instruction types; `"x" is not a Field Engine instruction`), **Import raw VM…** (paste or load a VM's JSON;
`A VM is an object with an "instructions" list.`), **Copy instructions as Python**; **Structure** (the
instructions in order); **Global parameters**; one block per instruction with a **Delete**, a **Help** fold
and its parameters (text boxes that commit on Enter or blur; a parameter whose values the engine knows offers
them as a list). A value the engine refuses is taken back and shown beside its box with the engine's message.

---

## 11. Monitor

Route `/monitor`. A passive subscriber to a TRex publisher: it listens on the server's async (PUB) port,
opens no RPC connection and changes nothing, so it works beside any other client and beside a stateful (-f)
run, which has no command port. It is independent of the Control session ([52](gui-inventory/screens/52-monitor-disconnected.png),
[95](gui-inventory/screens/95-monitor-guest-global.png) to [99](gui-inventory/screens/99-monitor-inspector.png)).

### 11.1 Connecting

**Host** (with the last ten hosts used, as `host:port`, in a drop-down), **Port** (4500), **Connect**,
**Disconnect**. The host starts as the Monitor's current target, the `--monitor` target, the `--server` host,
or `127.0.0.1`. **Connect** is off while connected (or connecting) to the host and port in the fields (`Already
connected to this publisher; change Host or Port to switch`); another host or port switches publisher, and a
stale connection can be restarted. **Disconnect** is off with no target.

The status line: `Monitor source <host>:<port>` (this page's own connection, not the Control session's in the
status bar), an LED and `Connected`, `Connecting…`, `Stale since HH:MM:SS` or `Disconnected`, then
`<n> frames · <n> bytes` and `dropped: <n> oversize, <n> undecodable, …` when the subscriber refused frames.
`Monitor data are not updating` tops the panels while the feed is stale.

### 11.2 Panels

Each panel appears once its message has arrived and can be collapsed from its heading (kept per browser):

- **Global**: tiles **Tx** (Tx bps, Tx pps, Tx cps, Packets sent, Bytes sent), **Rx** (Rx bps, Rx pps, Drop
  rate (Tx − Rx), flagged `drops` above 1 bps, with the Dashboard tile's tooltip, Packets received, Bytes received), **Flows** (Active flows, Open flows,
  and the NAT counters once they count), **System** (CPU and Rx CPU as meters, Alloc errors, Queue full,
  Queue drop); the table **Expected against measured** (bit rate, packet rate, connection rate, with
  `Measured / expected`); the charts **Monitor bits per second**, **Monitor packets per second**, **Monitor
  CPU** over 150 s, with the Dashboard's legend ([6](#6-dashboard)) and no Y axis control.
- **Ports**: Tx/Rx bps and pps, packets and errors in and out per port; the byte counts under a **Bytes** fold.
- **Templates** (a stateful run): each template's packets sent, and with rx-check its packets received,
  errors and jitter; a row that looks wrong says `check`.
- **Latency** (a stateful run with `-l`): per port and side, Avg, Max, Max (period), Errors, Jitter in µs; the
  chart **Max latency, client vs server (16 s)**.
- **Rx-check**: the engine's counters, and its histogram with **Scale** (Linear / Log) and **Bars**.
- **Latency v2**: the latency CPU, then per port a histogram, the summary counters and the port counters.
- **Inspector**: every name the publisher sends, with how long ago it last arrived; the chosen one as a tree
  (folded below two levels; the first 2000 nodes, `N more not shown`), a **Filter** on keys and **Copy**. A
  fraction below 0.0001 reads `≈ 0`, its exact value in the tooltip and in Copy. In `trex-global`, `ts` is the
  server's tick counter (`value`) and its ticks per second (`freq`), so `value` climbs with every message; its
  tooltip says so.

### 11.3 Starting the Monitor from the command line

`trex-web-gui serve --monitor HOST[:PORT]` attaches the Monitor at startup. A stateful run started from the daemon
dialog attaches it by itself ([12.3](#123-run-tab)).

### 11.4 Monitor-only backends

`trex-web-gui serve --monitor-only` serves the Monitor for a wall display: the top bar has **Monitor** and
**Settings** only, any other address goes to `/monitor`, the status line sits in the footer, Settings offers the
theme alone, and Ctrl+Shift+C is not bound. The backend answers 409 `This backend runs with --monitor-only` to every
request that changes state and to the capture, Studio, daemon, profile and file-browser routes.

---

## 12. TRex daemon dialog

**TRex daemon…** in the Control toolbar, available while disconnected too, because the daemon is how a TRex is
started before anyone can connect. The daemon (`trex_daemon_server`, port 8090) is not part of trex-web-gui; read
the README's "Daemon exposure" before using it outside the test VM. While the dialog is open the backend polls
the daemon; **Close** disconnects from it ([100](gui-inventory/screens/100-daemon-config.png) to
[105](gui-inventory/screens/105-daemon-stf-running.png)).

**Connect bar**: **Host** (the last daemon's host, else the Control session's host, else the `--server` host, else
`127.0.0.1`), **Port** (`--daemon-port`, 8090 by default), **Connect**, **Disconnect** (`Host is required`,
`Port must be 1–65535`). Opening the dialog
again reconnects to the last daemon. Under it the exposure warning, then `State: <not connected | connected |
error: …> · <version> · running since <time>`. Tabs **Config**, **YAML**, **Run** (the first shown), **Logs**.

### 12.1 Config tab

A form built from the daemon's own description of `trex_cfg.yaml` (`Connect to a daemon to edit its config;
the fields come from the daemon.`): the usual fields in a grid, the rest under **Advanced**; lists (the
interfaces, with the daemon's devices offered) have **Add …** and **Remove**; a problem opens the fold it is
in and is shown beside the field. **Load default** (the metadata's defaults, except the optional `platform`,
`memory` and `tw` sections and the `c` thread count, which it leaves to TRex's own defaults: a `platform` with only
the master thread, or `c: 4` on a small host, would stop TRex from starting) and **Load from server** (the
daemon's current file). The form opens on the same defaults. The YAML tab shows what the form describes.

### 12.2 YAML tab

The rendered config, read-only, with its problems listed; **Edit YAML** switches to a text box (back to the
form only once the text parses and passes); **Filename** (`trex-web-gui-<user>.yaml` by default; `The name must be
letters, digits, _ . -, end in .yaml or .yml, and not start with tg-`); **Push…** asks `Write <name> to <files
directory> on <host:port>?` with the YAML, then writes it to the daemon's files directory. A pushed file
becomes the Run tab's config path.

### 12.3 Run tab

**Configuration**: **Config path** (`/etc/trex_cfg.yaml`, or the file last pushed; `Config path must be
absolute, with no .., using only letters, digits and _ . / -`), **Cores (-c)** (1–64), **Start timeout (s)**
(5–300), **Mode**: **Stateless (interactive, -i)** or **Stateful run (-f)**.

- Stateless: **Extra arguments** (the ⓘ: up to 20 space-separated tokens of letters, digits and `_ . , : = /
  + -`; `--cfg`, `-i`, `--astf`, `-f`, `-d`, `-m`, `-l`, `--rx-check` and `--client_cfg` are refused, also
  abbreviated), **TRex command port** (prefilled from the pushed config's `zmq_rpc_port`, else the Control
  session's port, else 4501; the ⓘ: `the port <host> reaches the daemon's TRex on; Stop reads its port owners
  first`, `<host>` being the trex-web-gui host, or `the computer running trex-web-gui serve` until it is known).
- Stateful: **Source** (Stock, Uploaded) with **Upload…** ([12.5](#125-uploading-a-stateful-profile)),
  **Profile** (the daemon's list), the profile's details as the daemon reads it (templates, YAML duration,
  warnings, errors), **Duration -d (s)** (10–3600, prefilled from the YAML), **Multiplier -m** (0–1000),
  **Latency -l (pps, 0 = off)**, **Rx-check --rx-check (0 = off)**, **Monitor port** (the publisher port the
  Monitor attaches to; the ⓘ: `the PUB port <host> reaches the run's publisher on`).

**Command preview**: the exact command the daemon would run, refreshed as you type. **Status**: `is_running`,
`running_seq`, the TRex processes on the host, and `A TRex the daemon did not start blocks Start.`

**Start** (enabled when connected, nothing runs and the form previews; lit as **● TRex running** while a TRex
runs) asks `Start TRex on <host> (daemon
port N) with this command?` with the command; for a stateful run also `It runs as root on <host> for N s. A
stateful run has no command port, so Control cannot connect to it; watch it on the Monitor.`
([104](gui-inventory/screens/104-daemon-start-confirm.png)). A started stateful run shows the **Stateful run**
block: profile and duration, `ends in about MM:SS`, **Open Monitor**, and afterwards `Ended: <outcome>` or
`Stopped` with the **Final log** (the last 30 lines of TRex's log); the Monitor is attached to its publisher
by itself (`The run started, but the Monitor could not attach: …` otherwise).

**Stop** (a TRex this GUI started) asks `Stop the TRex this GUI started on <host>? Every client connected to
it, including this GUI's session, loses its connection.` **Force stop** (a TRex the daemon did not start)
asks `This TRex was not started by this GUI. Force stop kills the daemon's TRex on <host> whoever started it;
it is still refused while another user owns a port.`; when the owners cannot be read it asks once more,
`Force stop anyway?` (`TRex's command port did not answer, so the GUI cannot see who owns its ports…`, or for
a `-f` run `A stateful (-f) run has no command port, so the GUI cannot see who is using it…`).
`TRex is still running after the stop.` when the daemon reports so.

### 12.4 Logs tab

**Log**: `TRex log` or `Daemon log`, **Refresh**; the text, refreshed every 2 s while the tab is shown.

### 12.5 Uploading a stateful profile

**Upload…** browses the backend host for a stateful YAML; the dialog **Upload a stateful profile** reads the
profile and its pcaps on the backend host and lists **Template**, **Local file**, **Size**, **Remote name**,
**Already on the server**, the total and any warnings. **Push…** asks `Write N files (<size>) to <files
directory> on <daemon>? The daemon cannot delete them; they stay until the host clears <files directory>.`;
when everything is there already, **Use it**. The uploaded profile is then selected.

---

## 13. Settings

Route `/settings`, also Ctrl+Shift+P. A side list of sections; each saveable section has its own **Save** and
**Revert** (enabled once it has an edit) and is marked `· unsaved` in the list while it does; saving one never
sends another's edits ([53](gui-inventory/screens/53-settings-appearance.png) to
[57](gui-inventory/screens/57-settings-about.png)). `Settings changed on the server — Reload` appears above
the sections when another browser or an import saved different settings while you hold unsaved edits
([58](gui-inventory/screens/58-settings-changed-on-server.png)); with no edit the page takes the new values
by itself. When the settings could not be loaded, the error shows in red under the heading.

The settings live in `settings.toml` in the backend's data directory (`$XDG_DATA_HOME/trex-web-gui`, else
`~/.local/share/trex-web-gui`, or `--data-dir`); the data directory also holds the port names
(`port_labels.toml`), the test setups (`test_setups/`), the profile library (`profiles/`), the templates
(`templates/`), the capture spool (`captures/`) and the uploads (`uploads/`).

### 13.1 Appearance

**Theme**: **System**, **Light**, **Dark**, **High contrast**. Kept in this browser, applied at once, no Save.

### 13.2 Files on the backend host

**Templates directory** (your packet templates; empty means `<data dir>/templates`), **Workspace directory**
(where Browse starts), **Profile library directory** (ⓘ: `Empty: <data dir>/profiles (here: <data dir>/profiles).
Must be an absolute path on the backend host.`), **TRex config file (on the backend host)** (ⓘ: `Read at the
next connect.`; it is how the interface names are confirmed), **Wireshark path** (ⓘ: `Runs on the backend
host; needs a display there. A pcap download works anywhere.`), **Largest capture the packet editor opens
(MiB)** (ⓘ: `The pcap picker refuses a larger file before reading it. 1 to 1024; the default is 64. Uploads
from this computer are also limited to 64 MiB.`); **Save**, **Revert**, then `Saved` or the backend's message.

### 13.3 Session

**Release my ports when trex-web-gui exits** (ⓘ: `The Disconnect button always releases your ports.`); **Save**,
**Revert**.

### 13.4 Back up and restore

**Include theme**; **Export settings…** downloads `trex-web-gui-settings-<UTC stamp>.json` with the preferences,
saved connections, port labels and test setups (and the theme when ticked); **Import settings…** opens a file
chooser (at most 4 MiB; `<file> is not a settings file: …`), then the preview dialog
([61](gui-inventory/screens/61-import-preview.png) to [63](gui-inventory/screens/63-import-result.png),
[84](gui-inventory/screens/84-import-preview-540px.png)):

- **Import settings**: `Exported <time> by trex-web-gui <version>. Nothing is written until you press Apply, and
  an import runs nothing by itself.` A table **Section**, **Mode**, **What happens** for **Preferences**
  (replace or skip; skip by default, because preferences hold paths that run later), **Connections**, **Port
  labels**, **Test setups** (replace, merge or skip; merge by default). Every item that differs is named with
  what the mode does to it: `added`, `overwritten`, `kept (on this GUI, not in the file; replace would delete
  it)`, `deleted`, `not added`; `same as here, nothing to do`. Test setups add a fold **Before you Load these
  (N profiles, N re-pointed)**: the profile files a Load would run and the setups that would point at another
  file. **Also apply theme** when the file carries one. `Set <sections> to skip, or fix the file: a section
  with errors cannot be applied.`; `Switching to replace deletes what is kept`. **Cancel**, **Apply**.
- The result line: `Imported: <sections>` (` (theme applied)`), or `Import failed: <message> Written: …
  Failed: …` for a write that stopped half-way.

### 13.5 About

**Version**, **TRex client pin** (the vendored client's upstream SHA and the number of vendor patches),
**Supported server** (`STL API 5.1 (TRex v3.08)`), **Data directory (on the backend host)**.

Below them the page states two limits: "This backend accepts a browser only through the login URL it printed at start; anything that can read that terminal or journal can use it." and "It listens on 127.0.0.1 only: from another machine, use an SSH tunnel whose local port equals the serving port."

---

## 14. Why is this disabled? The common reasons

Disabled controls carry their reason as a tooltip, and on the Capture page and the Traffic and Config tabs as
visible text too. The checks run in this
order, so the first one you see is the first one to fix:

| Reason | Meaning | Fix |
|---|---|---|
| `not connected` | no live session | Connect… (status bar) |
| `read-only session` | connected as an observer | reconnect without Read-only |
| `select the default profile` | a non-default profile row is selected | select the port or its `_` profile |
| `acquire the port first` | another client, or nobody, owns the port | Acquire (or Force acquire) |
| `transmitting: stop traffic first` | the port is in TX, PCAP_TX or PAUSE; configuration needs IDLE, STREAMS or ASTF_LOADED | Stop |
| `busy: pinging` / `busy: scanning` / `busy: applying` | a ping, scan or Apply holds the port | wait |
| `filtered service mode: set it on or off in the Control tab` | ping, scan and Config need service mode on or off, not filtered | change service mode |
| `not supported by <driver>` | the NIC driver lacks the attribute | none |
| `Rx port(s) N are not in service mode` | captures need service mode on their Rx ports | Turn service mode on for ports N |

Commands refused by the backend show its message in the page's alert line, for example
`Not connected to a TRex server.`, `This session is read-only (observer mode). Reconnect without read-only to control
ports.`, `Port N is transmitting; stop traffic first.` or `Port N is owned by <user>; force-acquire to take it.`
A port held under your own user name by another session reads `Port N is held by another session of yours
(<user>); Re-acquire my ports takes it back.` instead.

## 15. What is kept, and for how long

| What | Where | Lifetime |
|---|---|---|
| Saved connections, preferences, port names, test setups | backend data directory (default `~/.local/share/trex-web-gui`) | until changed; part of Export settings |
| Studio library, user packet templates, saved capture files | backend data directory (or the directories set in Settings) | until deleted |
| Theme, last Browse directory, Monitor host history, collapsed Monitor panels, histogram bar count | this browser (local storage) | per browser |
| Traffic forms, Dashboard tab/interval/layout/Y axis, charted PG IDs, xstats pins and baselines, Clear stats baselines | this browser tab | until a new connection starts or the page is reloaded |
| Event log | this browser tab | across reconnects; lost on reload |
| RPC capture switch | backend session | survives a page reload |

---

## 16. Keyboard shortcuts

| Where | Keys | Action |
|---|---|---|
| anywhere | Ctrl+Shift+C | Connect dialog |
| anywhere | Ctrl+Shift+P | Settings |
| a dialog, a menu, a ⓘ note | Escape | close |
| a dialog | Tab / Shift+Tab | cycles inside the dialog |
| tab strips (port panel, Dashboard, Capture, Events, Tools, Settings, daemon, stream detail, Inspector) | ←/→ (↑/↓ when vertical), Home, End | move |
| the devices tree | ↑/↓, Home, End, Enter | move and select; Shift+F10 or ContextMenu opens the menu |
| a context menu | ↑/↓, Enter, Escape | move, run, close |
| the setups list, the library list | ↑/↓, Home, End | move |
| the capture packet table | ↑/↓, Enter | select, open the viewer |
| the stream table (focused) | Ctrl+C, Ctrl+V, Delete, F2, Alt+↑/↓, Ctrl+Z, Ctrl+Y or Ctrl+Shift+Z | copy, paste, delete, rename, move, undo, redo |
| the packet editor | Ctrl+Z; Ctrl+Y, Ctrl+Shift+Z or Ctrl+R; Ctrl+D | undo; redo; delete the layer opened last |
| the standalone packet editor | Ctrl+O, Ctrl+S | open a .trp, save a .trp |
| a field row | Enter, Escape | commit, revert the text |
| the hex view | ←/→/↑/↓, Home, End, Enter or Space, two hex digits, Escape | move, select the field, replace the byte, drop a half-typed byte |
| the stream table's rename box, the Save as… name row | Enter, Escape | save, cancel |

## 17. Screenshot index

`01` not authorised · `02` Control disconnected · `03`–`07` Connect dialog · `08` root selected · `09`, `10`
context menus · `11` Start all · `12` profile row · `13`–`15` Control tab · `16`, `17` Config · `18`–`20` Tools
· `21`, `23`–`25` Traffic · `22` Browse · `26`, `27` Counters · `28`–`30`, `33` Events · `31`, `32` connection
lost · `34`–`42` Dashboard · `43`–`50` Capture · `52`, `95`–`99` Monitor · `53`–`58` Settings · `59`, `60`
themes · `61`–`63`, `84` import · `64`–`82` setups · `83` backend offline · `85`–`87` state chips · `88`–`93`
Studio · `94` library pick · `100`–`105` daemon · `106` Connect with a stateful run · `107`–`112` packet
editor · `113` the editor in Studio · `114` a .py source. (`51` and `74` are unused.)

Not in any screenshot: a read-only session, another owner's ports, a non-default profile row, the LLDP table,
the Wireshark buttons in use, the pop-out Events window, the daemon's Logs tab, the Monitor's Templates,
Latency, Rx-check and Latency v2 panels on a live run (`97`–`99` are recorded frames), the payload dialog's
file modes, the field context menu, and the Import dialog for a `.py`.
