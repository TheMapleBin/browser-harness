"""CDP WS holder + IPC relay (Unix socket on POSIX, TCP loopback on Windows). One daemon per BU_NAME."""
import asyncio, json, os, socket, sys, time, urllib.error, urllib.request
from contextlib import suppress
from urllib.parse import urlparse
from collections import deque
from pathlib import Path

try:
    from websockets.exceptions import ConnectionClosed
except ImportError:
    ConnectionClosed = ()

from . import _ipc as ipc
from cdp_use.client import CDPClient


def _load_env():
    repo_root = Path(__file__).resolve().parents[2]
    workspace = Path(os.environ.get("BH_AGENT_WORKSPACE", repo_root / "agent-workspace")).expanduser()
    for p in (repo_root / ".env", workspace / ".env"):
        if not p.exists():
            continue
        _load_env_file(p)


def _load_env_file(p):
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

NAME = os.environ.get("BU_NAME", "default")
SOCK = ipc.sock_addr(NAME)
LOG = str(ipc.log_path(NAME))
PID = str(ipc.pid_path(NAME))
BUF = 500
PROFILES = [
    Path.home() / "Library/Application Support/Google/Chrome",
    Path.home() / "Library/Application Support/Google/Chrome Canary",
    Path.home() / "Library/Application Support/Comet",
    Path.home() / "Library/Application Support/Arc/User Data",
    Path.home() / "Library/Application Support/Dia/User Data",
    Path.home() / "Library/Application Support/Microsoft Edge",
    Path.home() / "Library/Application Support/Microsoft Edge Beta",
    Path.home() / "Library/Application Support/Microsoft Edge Dev",
    Path.home() / "Library/Application Support/Microsoft Edge Canary",
    Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser",
    Path.home() / ".config/google-chrome",
    Path.home() / ".config/chromium",
    Path.home() / ".config/chromium-browser",
    Path.home() / ".config/microsoft-edge",
    Path.home() / ".config/microsoft-edge-beta",
    Path.home() / ".config/microsoft-edge-dev",
    Path.home() / ".var/app/org.chromium.Chromium/config/chromium",
    Path.home() / ".var/app/com.google.Chrome/config/google-chrome",
    Path.home() / ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser",
    Path.home() / ".var/app/com.microsoft.Edge/config/microsoft-edge",
    Path.home() / "AppData/Local/Google/Chrome/User Data",
    Path.home() / "AppData/Local/Google/Chrome SxS/User Data",
    Path.home() / "AppData/Local/Chromium/User Data",
    Path.home() / "AppData/Local/Microsoft/Edge/User Data",
    Path.home() / "AppData/Local/Microsoft/Edge Beta/User Data",
    Path.home() / "AppData/Local/Microsoft/Edge Dev/User Data",
    Path.home() / "AppData/Local/Microsoft/Edge SxS/User Data",
    Path.home() / "AppData/Local/BraveSoftware/Brave-Browser/User Data",
]
INTERNAL = ("chrome://", "chrome-untrusted://", "devtools://", "chrome-extension://", "about:")
BU_API = "https://api.browser-use.com/api/v3"
REMOTE_ID = os.environ.get("BU_BROWSER_ID")
API_KEY = os.environ.get("BROWSER_USE_API_KEY")


def log(msg):
    open(LOG, "a").write(f"{msg}\n")


async def _silent(coro):
    try:
        await coro
    except Exception:
        pass


def _ws_from_devtools_active_port(http_url: str) -> str | None:
    """When /json/version returns 404 (Chrome 147+ default profile), match DevToolsActivePort by port."""
    p = urlparse(http_url)
    want_port = str(p.port) if p.port else ""
    if not want_port:
        return None
    host = p.hostname or "127.0.0.1"
    if ":" in host:  # urlparse strips IPv6 brackets; restore them for the ws:// URL
        host = f"[{host}]"
    for base in PROFILES:
        try:
            active = (base / "DevToolsActivePort").read_text().splitlines()
        except (FileNotFoundError, NotADirectoryError):
            continue
        port = active[0].strip() if active else ""
        ws_path = active[1].strip() if len(active) > 1 else ""
        if port == want_port and ws_path:
            return f"ws://{host}:{port}{ws_path}"
    return None


def get_ws_url(timeout=30):
    if url := os.environ.get("BU_CDP_WS"):
        return url
    if url := os.environ.get("BU_CDP_URL"):
        # HTTP DevTools endpoint (e.g. http://127.0.0.1:9333) — resolve to ws via /json/version.
        # Use this for a dedicated automation Chrome on a non-default profile, which avoids the
        # M144 "Allow remote debugging" dialog and the M136 default-profile lockdown.
        deadline = time.time() + timeout
        last_err = None
        base_url = url.rstrip("/")
        while time.time() < deadline:
            try:
                return json.loads(urllib.request.urlopen(f"{base_url}/json/version", timeout=5).read())["webSocketDebuggerUrl"]
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 404 and (ws := _ws_from_devtools_active_port(url)):
                    return ws
                time.sleep(1)
            except Exception as e:
                last_err = e
                time.sleep(1)
        raise RuntimeError(f"BU_CDP_URL={url} unreachable after 30s: {last_err} -- is the dedicated automation Chrome running?")
    for base in PROFILES:
        try:
            active = (base / "DevToolsActivePort").read_text().splitlines()
        except (FileNotFoundError, NotADirectoryError):
            continue
        port = active[0].strip() if active else ""
        ws_path = active[1].strip() if len(active) > 1 else ""
        if not port:
            continue
        # Resolve the live WS URL via /json/version instead of trusting the path stored
        # alongside the port in DevToolsActivePort: if Chrome was previously launched
        # with a different --user-data-dir on the same port, that file is left behind
        # with a stale browser UUID and the WS upgrade returns 404.
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1).read())["webSocketDebuggerUrl"]
            except urllib.error.HTTPError as e:
                # Chrome 147+ disables /json/* HTTP discovery on the default user-data-dir;
                # the ws path Chrome wrote to DevToolsActivePort still works.
                if e.code == 404 and ws_path:
                    return f"ws://127.0.0.1:{port}{ws_path}"
                time.sleep(1)
            except (OSError, KeyError, ValueError):
                time.sleep(1)
        raise RuntimeError(
            f"Chrome's remote-debugging page is open, but DevTools is not live yet on 127.0.0.1:{port} — if Chrome opened a profile picker, choose your normal profile first, then tick the checkbox and click Allow if shown"
        )
    for probe_port in (9222, 9223):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{probe_port}/json/version", timeout=1) as r:
                return json.loads(r.read())["webSocketDebuggerUrl"]
        except (OSError, KeyError, ValueError):
            continue
    raise RuntimeError(f"DevToolsActivePort not found in {[str(p) for p in PROFILES]} — enable chrome://inspect/#remote-debugging, or set BU_CDP_WS for a remote browser")


def stop_remote():
    if not REMOTE_ID or not API_KEY: return
    try:
        req = urllib.request.Request(
            f"{BU_API}/browsers/{REMOTE_ID}",
            data=json.dumps({"action": "stop"}).encode(),
            method="PATCH",
            headers={"X-Browser-Use-API-Key": API_KEY, "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15).read()
        log(f"stopped remote browser {REMOTE_ID}")
    except Exception as e:
        log(f"stop_remote failed ({REMOTE_ID}): {e}")


def is_real_page(t):
    return t["type"] == "page" and not t.get("url", "").startswith(INTERNAL)


class Daemon:
    def __init__(self):
        self.cdp = None
        self.session = None
        self.target_id = None
        self.events = deque(maxlen=BUF)
        self.dialog = None
        self.stop = None  # asyncio.Event, set inside start()
        self._connecting_task = None
        self._ever_connected = False
        self._ready = False
        self._last_connect_error = None
        self._connect_phase = "idle"
        self._connect_attempt = 0
        self._next_retry_at = None

    async def attach_first_page(self):
        """Attach to a real page (or any page). Sets self.session. Returns attached target or None."""
        if not self.cdp:
            return None
        targets = (await self.cdp.send_raw("Target.getTargets"))["targetInfos"]
        pages = [t for t in targets if is_real_page(t)]
        if not pages:
            # No real pages. Create a placeholder page. Must NOT use
            # about:blank — INTERNAL filtering strips about: URLs, so the
            # next attach_first_page() call would fail to find this page
            # and create another, leaking tabs.
            placeholder = "data:text/html,<title>Browser Harness</title>"
            tid = (await self.cdp.send_raw("Target.createTarget", {"url": placeholder}))["targetId"]
            log(f"no real pages found, created placeholder ({tid})")
            pages = [{"targetId": tid, "url": placeholder, "type": "page"}]
        self.session = (await self.cdp.send_raw(
            "Target.attachToTarget", {"targetId": pages[0]["targetId"], "flatten": True}
        ))["sessionId"]
        self.target_id = pages[0]["targetId"]
        log(f"attached {pages[0]['targetId']} ({pages[0].get('url','')[:80]}) session={self.session}")
        await self._enable_default_domains(self.session)
        return pages[0]

    async def _enable_default_domains(self, session_id):
        """Enable Page/DOM/Runtime/Network on a CDP session.

        Used by both initial attach and set_session (called after switch_tab/
        new_tab). Without this, helpers that depend on Network.* events —
        notably wait_for_network_idle() — silently stop receiving events
        after a tab switch, because each fresh CDP session starts with all
        domains disabled.

        Runs the four enables in parallel via gather so the worst-case time is
        bounded by a single CDP round trip rather than four sequential ones —
        important on the set_session path, where the helper's IPC socket has
        a 5s read timeout.
        """
        async def enable_one(d):
            try:
                await asyncio.wait_for(
                    self.cdp.send_raw(f"{d}.enable", session_id=session_id),
                    timeout=4,
                )
            except Exception as e:
                log(f"enable {d} on {session_id}: {e}")
        await asyncio.gather(*(enable_one(d) for d in ("Page", "DOM", "Runtime", "Network")))

    # ── lifecycle state ──────────────────────────────
    @property
    def _status(self):
        if self._ready and self.cdp:
            return "ready"
        if self._ever_connected:
            return "reconnecting"
        return "connecting"

    def _clear_cdp_state(self):
        self.cdp = None
        self.session = None
        self.target_id = None
        self.dialog = None
        self._ready = False

    async def _sleep_or_stopped(self, seconds):
        if not self.stop:
            await asyncio.sleep(seconds)
            return False
        try:
            await asyncio.wait_for(self.stop.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _connect_cdp(self):
        backoff = 0.5
        max_backoff = 30.0
        while not (self.stop and self.stop.is_set()):
            cdp = None
            try:
                self._connect_attempt += 1
                self._connect_phase = "resolving_ws_url"
                self._next_retry_at = None
                url = await asyncio.to_thread(lambda: get_ws_url(timeout=5))
                log(f"connecting to {url}")
                self._connect_phase = "starting_cdp_client"
                cdp = CDPClient(url)
                await cdp.start()
                self.cdp = cdp
                self._ready = False
                await self.attach_first_page()
                self._wire_event_tap(cdp)
                self._connect_phase = "attached"
                self._ever_connected = True
                self._ready = True
                self._last_connect_error = None
                log("CDP connected")
                return
            except asyncio.CancelledError:
                if cdp:
                    with suppress(Exception):
                        await cdp.stop()
                raise
            except Exception as e:
                self._connect_phase = "backing_off"
                self._last_connect_error = str(e)
                self._next_retry_at = time.time() + backoff
                log(f"CDP connect failed: {e} — retrying in {backoff:.1f}s")
                if cdp:
                    with suppress(Exception):
                        await cdp.stop()
                self._clear_cdp_state()
                if await self._sleep_or_stopped(backoff):
                    return
                backoff = min(backoff * 2, max_backoff)

    def _wire_event_tap(self, cdp):
        orig = cdp._event_registry.handle_event
        mark_js = "if(!document.title.startsWith('\U0001F434'))document.title='\U0001F434 '+document.title"
        async def tap(method, params, session_id=None):
            self.events.append({"method": method, "params": params, "session_id": session_id})
            if method == "Page.javascriptDialogOpening":
                self.dialog = params
            elif method == "Page.javascriptDialogClosed":
                self.dialog = None
            elif (
                method in ("Page.loadEventFired", "Page.domContentEventFired")
                and self.cdp is cdp
                and self.session
            ):
                asyncio.create_task(_silent(asyncio.wait_for(
                    cdp.send_raw("Runtime.evaluate", {"expression": mark_js}, session_id=self.session),
                    timeout=2,
                )))
            return await orig(method, params, session_id)
        cdp._event_registry.handle_event = tap

    def _ensure_connecting(self):
        if self.stop and self.stop.is_set():
            return
        if self._connecting_task and not self._connecting_task.done():
            return
        self._connecting_task = asyncio.create_task(self._connect_cdp())

    def _is_cdp_disconnect(self, exc):
        seen = set()
        cur = exc
        while cur and id(cur) not in seen:
            seen.add(id(cur))
            if isinstance(cur, ConnectionClosed):
                return True
            if isinstance(cur, ConnectionError):
                msg = str(cur).lower()
                if "websocket connection closed" in msg or "client is stopping" in msg:
                    return True
            cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
        msg = str(exc).lower()
        return (
            "websocket connection closed" in msg
            or "no close frame received" in msg
            or "received 1006" in msg
        )

    def _mark_disconnected(self, exc=None):
        self._connect_phase = "disconnected"
        if exc:
            self._last_connect_error = str(exc)
        old = self.cdp
        self._clear_cdp_state()
        if old:
            asyncio.create_task(_silent(old.stop()))
        self._ensure_connecting()

    async def start(self):
        self.stop = asyncio.Event()
        self._ensure_connecting()

    async def handle(self, req):
        expected = ipc.expected_token()
        if expected is not None and req.get("token") != expected:
            return {"error": "unauthorized"}
        meta = req.get("meta")

        if meta == "ping":
            return {"pong": True, "pid": os.getpid()}
        if meta == "status":
            return {
                "status": self._status, "ready": self._status == "ready",
                "last_error": self._last_connect_error,
                "phase": self._connect_phase,
                "attempt": self._connect_attempt,
                "next_retry_at": self._next_retry_at,
            }
        if meta == "drain_events":
            out = list(self.events); self.events.clear()
            return {"events": out}
        if meta == "shutdown":
            self.stop.set(); return {"ok": True}

        if meta == "session":
            return {"session_id": self.session, "status": self._status}
        if meta == "pending_dialog":
            return {"dialog": self.dialog}

        if meta == "current_tab":
            if not self._ready or not self.cdp or not self.target_id:
                return {"error": "cdp_disconnected", "status": self._status, "last_error": self._last_connect_error}
            try:
                info = (await self.cdp.send_raw("Target.getTargetInfo", {"targetId": self.target_id}))["targetInfo"]
            except Exception as e:
                if self._is_cdp_disconnect(e):
                    self._mark_disconnected(e)
                    return {"error": "cdp_disconnected", "status": self._status, "last_error": self._last_connect_error}
                return {"error": str(e), "status": self._status}
            return {"targetId": info.get("targetId"), "url": info.get("url", ""), "title": info.get("title", "")}

        if meta == "connection_status":
            if not self._ready or not self.cdp or not self.target_id:
                return {"error": "cdp_disconnected", "status": self._status, "last_error": self._last_connect_error}
            try:
                info = (await self.cdp.send_raw("Target.getTargetInfo", {"targetId": self.target_id}))["targetInfo"]
            except Exception as e:
                if self._is_cdp_disconnect(e):
                    self._mark_disconnected(e)
                    return {"error": "cdp_disconnected", "status": self._status, "last_error": self._last_connect_error}
                return {"error": str(e), "status": self._status}
            page = None
            if is_real_page(info):
                page = {
                    "targetId": info.get("targetId"),
                    "title": info.get("title") or "(untitled)",
                    "url": info.get("url") or "",
                }
            return {"target_id": self.target_id, "session_id": self.session, "page": page, "status": "ready"}

        if meta == "set_session":
            if not self._ready or not self.cdp:
                return {"error": "cdp_disconnected", "status": self._status, "last_error": self._last_connect_error}
            old_session = self.session
            self.session = req.get("session_id")
            self.target_id = req.get("target_id") or self.target_id
            tasks = []
            if old_session and old_session != self.session:
                async def disable_old():
                    try:
                        await asyncio.wait_for(
                            self.cdp.send_raw("Network.disable", session_id=old_session),
                            timeout=2,
                        )
                    except Exception: pass
                tasks.append(disable_old())
            tasks.append(self._enable_default_domains(self.session))
            await asyncio.gather(*tasks)
            asyncio.create_task(_silent(asyncio.wait_for(
                self.cdp.send_raw(
                    "Runtime.evaluate",
                    {"expression": "if(!document.title.startsWith('\U0001F434'))document.title='\U0001F434 '+document.title"},
                    session_id=self.session,
                ),
                timeout=2,
            )))
            return {"session_id": self.session, "status": self._status}

        method = req["method"]
        params = req.get("params") or {}
        sid = None if method.startswith("Target.") else (req.get("session_id") or self.session)

        if not self._ready or not self.cdp:
            return {"error": "cdp_disconnected", "status": self._status, "last_error": self._last_connect_error}

        try:
            return {"result": await self.cdp.send_raw(method, params, session_id=sid)}
        except Exception as e:
            msg = str(e)
            if "Session with given id not found" in msg and sid == self.session and sid:
                log(f"stale session {sid}, re-attaching")
                try:
                    if await self.attach_first_page():
                        return {"result": await self.cdp.send_raw(method, params, session_id=self.session)}
                except Exception as reattach_error:
                    if self._is_cdp_disconnect(reattach_error):
                        self._mark_disconnected(reattach_error)
                        return {"error": "cdp_disconnected", "status": self._status, "last_error": self._last_connect_error}
                    return {"error": str(reattach_error), "status": self._status}
            if self._is_cdp_disconnect(e):
                log(f"CDP disconnected: {e} — scheduling reconnect")
                self._mark_disconnected(e)
                return {"error": "cdp_disconnected", "status": self._status, "last_error": self._last_connect_error}
            return {"error": msg, "status": self._status}

    async def close(self):
        if self._connecting_task and not self._connecting_task.done():
            self._connecting_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._connecting_task
        if self.cdp:
            with suppress(Exception):
                await self.cdp.stop()
        self._clear_cdp_state()


async def serve(d):
    async def handler(reader, writer):
        try:
            line = await reader.readline()
            if not line: return
            resp = await d.handle(json.loads(line))
            writer.write((json.dumps(resp, default=str) + "\n").encode())
            await writer.drain()
        except Exception as e:
            log(f"conn: {e}")
            try:
                writer.write((json.dumps({"error": str(e)}) + "\n").encode())
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()

    serve_task = asyncio.create_task(ipc.serve(NAME, handler))
    stop_task = asyncio.create_task(d.stop.wait())
    await asyncio.sleep(0.05)  # let serve() bind so sock_addr() resolves to the live endpoint
    log(f"listening on {ipc.sock_addr(NAME)} (name={NAME}, remote={REMOTE_ID or 'local'})")
    try:
        await asyncio.wait({serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if serve_task.done(): await serve_task  # surfaces a serve crash
    finally:
        for t in (serve_task, stop_task):
            t.cancel()
            try: await t
            except (asyncio.CancelledError, Exception): pass
        ipc.cleanup_endpoint(NAME)


async def main():
    d = Daemon()
    await d.start()
    try:
        await serve(d)
    finally:
        await d.close()


def already_running():
    # Ping handshake (not a bare connect) so a stale .port file + port reuse
    # after a daemon crash doesn't make us mistake an unrelated listener for ours.
    return ipc.ping(NAME, timeout=1.0)


if __name__ == "__main__":
    if already_running():
        print(f"daemon already running on {SOCK}", file=sys.stderr)
        sys.exit(0)
    open(LOG, "w").close()
    open(PID, "w").write(str(os.getpid()))
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log(f"fatal: {e}")
        sys.exit(1)
    finally:
        stop_remote()
        try: os.unlink(PID)
        except FileNotFoundError: pass
