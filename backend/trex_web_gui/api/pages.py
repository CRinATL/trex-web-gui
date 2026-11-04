"""The HTML error page a browser gets when a person navigates to a refusal.

Every refusal is `{error, message}` JSON for the GUI's fetch calls, scripts and curl (api/bodies.py `err`),
and that shape and its status codes are the contract. A person can reach some of the same refusals by
navigating a tab there: a login URL from an earlier run (403 `bad_token`), a bookmarked `/api` path (401
`unauthenticated`, 404), the wrong host name (400), or an unhandled exception (500). A raw JSON body is a poor
answer to a person, so for a top-level navigation the same refusal is this page: the same status, a plain
statement of what happened, what to check, the code small for support, and the project's links.

`wants_html` is the negotiation: `Accept` lists `text/html`, and when the browser sends `Sec-Fetch-Dest` it
says `document` (a fetch from the GUI says `empty`, and is answered JSON even if it asked for HTML). The page
is self-contained (inline CSS, no script but the GUI's own theme pre-paint, no font or CDN: the backend runs
on hosts that are often offline) and follows the GUI's theme tokens (frontend/src/index.css), including the
`trex-web-gui.theme` choice stored by the GUI. The message is HTML-escaped; the query string, and so the
token, never reaches the page (see auth.RedactLoginToken for the access log)."""
import json
from html import escape

from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.types import Scope

PROJECT_URL = "https://github.com/CRinATL/trex-web-gui"
ACCESS_URL = PROJECT_URL + "#access-single-user-localhost-only"
ISSUES_URL = PROJECT_URL + "/issues"
API_DOC_URL = PROJECT_URL + "/blob/main/docs/api.md"

# One hint is a lead sentence and its detail; both are HTML written here, never request data.
Hint = tuple[str, str]
# code -> (the headline, the sentence under it, the hints)
Page = tuple[str, str, tuple[Hint, ...]]

_LOGIN_URL = "<code>Open: http://127.0.0.1:8765/api/auth/login?token=…</code>"
_SERVICE_LOG = "Run as a service, it is in <code>journalctl --user -u trex-web-gui</code>."
_GUI_LINK = ("Looking for the GUI?",
             "It is at the root of this address, <a href=\"/\">/</a>, not under <code>/api</code>.")
_API_DOC = ("The API reference",
            f"<a href=\"{API_DOC_URL}\">docs/api.md</a> lists every route and what it answers.")

PAGES: dict[str, Page] = {
    "bad_token": (
        "This login link doesn't match the running backend.",
        "The link carries an access token, and it is not the one this backend was started with.",
        (("Use the newest login URL.",
          f"trex-web-gui prints one when it starts, {_LOGIN_URL}. The token changes every time the backend "
          "restarts, so a link from an earlier run no longer works, unless <code>TREX_WEB_GUI_TOKEN</code> "
          f"pins it. {_SERVICE_LOG}"),
         ("Check that the whole URL was copied.",
          "The token is the last part of the address, after <code>token=</code>. A line break or a missing "
          "character at the end is enough to break it."),
         ("Check the host and port.",
          "A link belongs to one backend. If more than one is running, or you reach it through an SSH "
          "tunnel, the port in the address must be the one that backend printed."))),
    "unauthenticated": (
        "This browser isn't signed in to this backend.",
        "The address is part of trex-web-gui's API, and this browser holds no session for it.",
        (("Open the login URL first.",
          f"trex-web-gui prints one when it starts, {_LOGIN_URL}. Opening it signs this browser in, and the "
          f"GUI loads. {_SERVICE_LOG}"),
         ("A restart ends every session.",
          "The token changes when the backend restarts, unless <code>TREX_WEB_GUI_TOKEN</code> pins it, so "
          "open the newest URL it printed."),
         _GUI_LINK)),
    "forbidden_origin": (
        "A page on another site tried to use this backend.",
        "Browsers tell trex-web-gui which page a request came from, and this one came from a page it does "
        "not trust, so it was refused. That is its cross-site protection working.",
        (("Open the GUI at its own address.",
          "The one the backend printed: <code>http://127.0.0.1:&lt;port&gt;</code>, or through an SSH tunnel "
          "<code>http://localhost:&lt;port&gt;</code>."),
         ("Keep the port the same through a tunnel.",
          "Only the backend's own port is accepted, so the tunnel's local port must equal it "
          "(<code>ssh -N -L 8765:127.0.0.1:8765 …</code>)."),
         ("Not from another page.",
          "trex-web-gui does not let another local web app, or a page that embeds it, drive it."))),
    "bad_host": (
        "This backend doesn't answer to that address.",
        "trex-web-gui serves only the loopback names, and the host name in the address bar is not one of "
        "them.",
        (("Use 127.0.0.1 or localhost.",
          "<code>http://127.0.0.1:8765</code> or <code>http://localhost:8765</code> (your port may differ). "
          "A machine name, <code>0.0.0.0</code> or <code>[::1]</code> is refused even when it reaches the "
          "same process."),
         ("Through an SSH tunnel, use the printed URL.",
          "The backend prints the URL for the other end, and it already says <code>localhost</code>."))),
    "not_found": (
        "There's nothing at this address.",
        "The path is under trex-web-gui's API, and no route answers it.",
        (("Check the address.", "A letter missing from a bookmark or a pasted link is enough."),
         _GUI_LINK, _API_DOC)),
    "method_not_allowed": (
        "This address can't be opened as a page.",
        "The route exists, but it does not answer a plain page load: it is for the GUI's own requests.",
        (_GUI_LINK, _API_DOC)),
    "internal": (
        "Something went wrong inside the backend.",
        "The request reached trex-web-gui and failed with an error it did not expect. The backend is still "
        "running.",
        (("Try again.", "If it fails the same way, the error below says where."),
         ("Read the backend's output.",
          f"The traceback is in the terminal that runs trex-web-gui. {_SERVICE_LOG}"),
         ("Report it.",
          f"<a href=\"{ISSUES_URL}\">Open an issue</a> with the error below and the traceback."))),
}
GENERIC: Page = (
    "trex-web-gui couldn't handle this request.",
    "The backend refused it; the error below says why.",
    (_GUI_LINK, _API_DOC))

# The GUI's theme tokens (frontend/src/index.css: :root, the dark block, the three data-theme blocks), the
# shell's top bar and its port-state badge; nothing loads from the network.
_CSS = """
:root{color-scheme:light;--bg:#fff;--fg:#1d1d22;--muted:#6b6b76;--border:#d9d9e0;--panel:#f4f4f8;
--accent:#3b5bdb;--bad:#c92a2a;font:15px/1.5 system-ui,sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme]){color-scheme:dark;--bg:#16161a;--fg:#e8e8ee;
--muted:#9a9aa8;--border:#33333c;--panel:#202026;--accent:#8aa2ff;--bad:#ff6b6b}}
:root[data-theme=light]{color-scheme:light}
:root[data-theme=dark]{color-scheme:dark;--bg:#16161a;--fg:#e8e8ee;--muted:#9a9aa8;--border:#33333c;
--panel:#202026;--accent:#8aa2ff;--bad:#ff6b6b}
:root[data-theme=contrast]{color-scheme:dark;--bg:#000;--fg:#fff;--muted:#c8c8c8;--border:#fff;--panel:#0a0a0a;
--accent:#6cb6ff;--bad:#ff7b7b}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%}
body{background:var(--bg);color:var(--fg);display:flex;flex-direction:column;min-height:100vh}
header{display:flex;align-items:center;gap:1rem;padding:.5rem 1rem;background:var(--panel);
border-bottom:1px solid var(--border)}
.wordmark{color:var(--fg);font-weight:600;text-decoration:none}
.badge{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.8rem;padding:0 .55rem;
border-radius:999px;border:1px solid var(--bad);color:var(--bad);white-space:nowrap}
main{flex:1;width:100%;max-width:42rem;margin:0 auto;padding:2.5rem 1rem 1.5rem}
h1{font-size:1.65rem;line-height:1.25;font-weight:600;letter-spacing:-.01em;margin:0 0 .75rem;
text-wrap:balance}
.intro{margin:0 0 2rem;max-width:60ch}
h2{font-size:1rem;font-weight:600;margin:0 0 .25rem}
.hints{list-style:none;margin:0;padding:0;border-top:1px solid var(--border)}
.hints li{padding:.75rem 0;border-bottom:1px solid var(--border);max-width:70ch}
.hints strong{display:block;font-weight:600}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.9em;background:var(--panel);
border-radius:3px;padding:0 .25em;overflow-wrap:anywhere}
.said{margin:1.5rem 0 0;color:var(--muted);font-size:.9rem;overflow-wrap:anywhere}
.said code{color:var(--fg)}
a{color:var(--accent)}
a:focus-visible,.wordmark:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
footer{width:100%;max-width:42rem;margin:0 auto;padding:0 1rem 2rem;color:var(--muted);font-size:.85rem}
footer nav{display:flex;flex-wrap:wrap;gap:.25rem 1.5rem;padding-top:.75rem;
border-top:1px solid var(--border)}
footer a{color:inherit}
"""

# frontend/index.html's pre-paint script, so a Dark or High-contrast choice made in the GUI carries over.
_THEME_SCRIPT = ("try{var t=localStorage.getItem('trex-web-gui.theme');"
                 "if(t==='light'||t==='dark'||t==='contrast')document.documentElement.dataset.theme=t}catch(e){}")


def wants_html(scope: Scope) -> bool:
    """True when the request is a browser navigation: `Accept` lists `text/html` and, when `Sec-Fetch-Dest`
    is sent, it is `document`. A fetch or XHR (no `text/html`, or a `Sec-Fetch-Dest` of `empty`), curl and the
    test client (`*/*`) get JSON. Headers are read from the raw ASGI scope, so a middleware can ask too."""
    accept, dest = "", None
    for k, v in scope.get("headers", ()):
        if k == b"accept":
            accept = v.decode("latin-1")
        elif k == b"sec-fetch-dest":
            dest = v.decode("latin-1").strip().lower()
    types = {part.split(";", 1)[0].strip().lower() for part in accept.split(",")}
    return "text/html" in types and (dest is None or dest == "document")


def render(status: int, code: str, message: str) -> str:
    """The page for one refusal: the headline and hints of `code` (PAGES, or GENERIC for a code without
    its own; both are HTML written here), the status and code in the badge, and `message` escaped in the
    support line."""
    title, intro, hints = PAGES.get(code, GENERIC)
    items = "".join(f"<li><strong>{lead}</strong> {detail}</li>" for lead, detail in hints)
    label = f"{status} {escape(code)}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<script>{_THEME_SCRIPT}</script>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{title} – trex-web-gui</title>
<style>{_CSS}</style>
</head>
<body>
<header>
<a class="wordmark" href="/">trex-web-gui</a>
<span class="badge" aria-label="error {label}">{label}</span>
</header>
<main>
<h1>{title}</h1>
<p class="intro">{intro}</p>
<h2>What to check</h2>
<ul class="hints">{items}</ul>
<p class="said">The backend answered <code>{label}</code>: {escape(message)}</p>
</main>
<footer><nav aria-label="trex-web-gui links">
<a href="{PROJECT_URL}">trex-web-gui on GitHub</a>
<a href="{ACCESS_URL}">Access and login (README)</a>
<a href="{ISSUES_URL}">Report a problem</a>
</nav></footer>
</body>
</html>
"""


def html_error(status: int, code: str, message: str) -> HTMLResponse:
    """`render` as an HTML response with status `status`; the caller has checked `wants_html`."""
    return HTMLResponse(render(status, code, message), status_code=status)


def for_browser(scope: Scope, response: JSONResponse) -> Response:
    """`response`, an `err(status, code, message)` JSON refusal, as it is; or, when the request is a browser
    navigation (`wants_html`), the HTML page with the same status, code and message. The JSON twin stays
    the one place a refusal's status and code are written (scripts/check_api_errors.py reads it there)."""
    if not wants_html(scope):
        return response
    body = json.loads(bytes(response.body))
    return html_error(response.status_code, str(body["error"]), str(body["message"]))
