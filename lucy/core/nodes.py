"""Node network — Phase 1.

Remote devices run lucy/node/node.py, connect to ws://core:8000/node,
authenticate with the shared node token, announce their self-probed
capabilities, then heartbeat and execute dispatched tasks.
"""
import asyncio, hmac, json, re, secrets, socket, subprocess, time, uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import PlainTextResponse, FileResponse

from . import config, db
from .registry import registry

router = APIRouter()

_AGENT_PATH = config.ROOT / "lucy" / "node" / "node.py"


def agent_version():
    m = re.search(r'AGENT_VERSION\s*=\s*"([\d.]+)"',
                  _AGENT_PATH.read_text(encoding="utf-8"))
    return m.group(1) if m else "0.0.0"


# ─── mDNS advertising ─────────────────────────────────────
_zeroconf = None


def _register_mdns_sync():
    global _zeroconf
    try:
        from zeroconf import Zeroconf, ServiceInfo
    except ImportError:
        print("mDNS off (pip install zeroconf to enable)")
        return
    try:
        ip = lan_ip()
        info = ServiceInfo(
            config.MDNS_SERVICE, f"lucy-core.{config.MDNS_SERVICE}",
            addresses=[socket.inet_aton(ip)], port=config.PORT,
            properties={"role": "core"})
        _zeroconf = Zeroconf()
        _zeroconf.register_service(info)
        print(f"mDNS: advertising lucy-core at {ip}:{config.PORT}")
    except Exception as e:
        print(f"mDNS registration failed: {type(e).__name__}: {e}")


async def register_mdns():
    # zeroconf's sync API must not be driven from inside the event loop
    await asyncio.to_thread(_register_mdns_sync)


def unregister_mdns():
    global _zeroconf
    if _zeroconf:
        try:
            _zeroconf.close()
        except Exception:
            pass
        _zeroconf = None


# ─── Token ────────────────────────────────────────────────
def get_node_token():
    config.DATA_DIR.mkdir(exist_ok=True)
    if not config.NODE_TOKEN_PATH.exists():
        config.NODE_TOKEN_PATH.write_text(secrets.token_hex(24), encoding="utf-8")
    return config.NODE_TOKEN_PATH.read_text(encoding="utf-8").strip()


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def print_pairing_line():
    token = get_node_token()
    url = f"http://{lan_ip()}:{config.PORT}"
    print("-- node pairing (run on the new device) --")
    print(f"  $env:LUCY_TOKEN='{token}'; irm {url}/node/bootstrap.ps1 | iex")


# ─── Known devices (for Lucy-initiated wake) ──────────────
# Every node that ever joins gets its LAN ip + MAC recorded, so the
# "Connect devices through Lucy" setting can Wake-on-LAN it later even
# while it's asleep and the live connection is gone.
def _load_known():
    try:
        return json.loads(config.KNOWN_NODES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_known(data):
    config.DATA_DIR.mkdir(exist_ok=True)
    config.KNOWN_NODES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


_MAC_RE = re.compile(r"([0-9a-f]{2}[-:]){5}[0-9a-f]{2}", re.I)


def _arp_mac(ip):
    """MAC for a LAN ip from the ARP cache — warm, since the node just
    opened a TCP connection to us. None off-subnet or on parse failure."""
    try:
        out = subprocess.run(["arp", "-a", ip], capture_output=True,
                             text=True, timeout=5).stdout
        m = _MAC_RE.search(out)
        return m.group(0).replace("-", ":").lower() if m else None
    except Exception:
        return None


def remember_node(name, ip):
    if not ip or ip.startswith("127."):
        return
    mac = _arp_mac(ip)
    known = _load_known()
    entry = known.get(name, {})
    entry["ip"] = ip
    if mac:
        entry["mac"] = mac
    entry["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
    known[name] = entry
    _save_known(known)
    print(f"Node remembered: {name} ip={ip} mac={mac or entry.get('mac', 'unknown')}")


def known_nodes():
    return _load_known()


def set_nickname(name, nickname):
    """Attach a user-chosen nickname to a device. The real name stays the
    routing key everywhere; the nickname is a display-boundary concern."""
    known = _load_known()
    entry = known.get(name, {})
    if nickname:
        entry["nickname"] = nickname
    else:
        entry.pop("nickname", None)
    known[name] = entry
    _save_known(known)


def device_label(name):
    """What to show/say for a device: its nickname if one is set."""
    return _load_known().get(name, {}).get("nickname") or name


def send_wol(mac):
    """Broadcast a Wake-on-LAN magic packet (both common ports, thrice)."""
    clean = mac.replace(":", "").replace("-", "")
    payload = bytes.fromhex("FF" * 6 + clean * 16)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        for port in (9, 7):
            for _ in range(3):
                s.sendto(payload, ("255.255.255.255", port))
    finally:
        s.close()


# ─── Node connection ──────────────────────────────────────
class NodeConn:
    def __init__(self, ws: WebSocket, name: str, hello: dict):
        self.ws = ws
        self.name = name
        self.hello = hello
        self.stats = {}
        self.last_seen = time.time()
        self.pending = {}  # task_id -> Future

    async def dispatch(self, capability, action, params, idem=None, timeout=None, task_id=None):
        task_id = task_id or uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self.pending[task_id] = fut
        try:
            await self.ws.send_json({
                "v": 1, "type": "task", "task_id": task_id,
                "capability": capability, "action": action, "params": params,
                "idempotency_key": idem or task_id,
                "timeout_s": timeout or config.TASK_TIMEOUT_S,
            })
            result = await asyncio.wait_for(fut, timeout or config.TASK_TIMEOUT_S)
        finally:
            self.pending.pop(task_id, None)
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "node task failed"))
        return result.get("data")

    def fail_pending(self, reason):
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(reason))
        self.pending.clear()


class NodeManager:
    def __init__(self):
        self.nodes: dict[str, NodeConn] = {}

    def unique_name(self, base):
        name, i = base, 2
        while name in self.nodes:
            name = f"{base}-{i}"
            i += 1
        return name

    def snapshot(self):
        out = []
        for n in self.nodes.values():
            out.append({
                "name": n.name,
                "kind": "remote",
                "alive": (time.time() - n.last_seen) < config.NODE_STALE_SECS,
                "os": n.hello.get("os", "?"),
                "agent": n.hello.get("agent_version", "0.1.x"),
                "capabilities": [c["capability"] for c in n.hello.get("capabilities", [])],
                "stats": n.stats,
            })
        return out


node_manager = NodeManager()


# ─── WS endpoint for node agents ──────────────────────────
@router.websocket("/node")
async def node_ws(ws: WebSocket):
    await ws.accept()
    conn = None
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=10)
        if hello.get("type") != "hello" or not hmac.compare_digest(
                str(hello.get("token", "")), get_node_token()):
            await ws.send_json({"type": "auth_failed"})
            await ws.close()
            return

        base_name = hello.get("node", "node")
        if base_name in node_manager.nodes:
            # same machine reconnecting (lid close, network blip) — take over
            old = node_manager.nodes.pop(base_name)
            old.fail_pending("replaced by reconnect")
            registry.remove_remote(base_name)
            try:
                await old.ws.close()
            except Exception:
                pass
            name = base_name
            print(f"Node reconnected: {name} (old session replaced)")
        else:
            name = base_name
        conn = NodeConn(ws, name, hello)
        node_manager.nodes[name] = conn
        db.upsert_node(name, hello.get("os", "?"), hello.get("arch", "?"))
        for cap_manifest in hello.get("capabilities", []):
            registry.add_remote(conn, cap_manifest)
        caps = ", ".join(c["capability"] for c in hello.get("capabilities", []))
        print(f"Node joined: {name} [{caps}] agent v{hello.get('agent_version', '0.1.x')}")
        await ws.send_json({"type": "welcome", "name": name, "v": 1,
                            "agent_version": agent_version()})
        client_ip = ws.client.host if ws.client else None
        asyncio.create_task(asyncio.to_thread(remember_node, name, client_ip))

        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            conn.last_seen = time.time()
            if mtype == "heartbeat":
                conn.stats = msg.get("stats", {})
            elif mtype == "result":
                fut = conn.pending.get(msg.get("task_id"))
                if fut and not fut.done():
                    fut.set_result(msg)
            elif mtype == "task_progress":
                registry.emit("task_progress", {
                    "task_id": msg.get("task_id"), "node": conn.name,
                    "done": msg.get("done"), "total": msg.get("total"),
                    "note": msg.get("note", ""),
                })

    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception as e:
        print(f"Node WS error: {e}")
    finally:
        if conn:
            conn.fail_pending("node disconnected")
            node_manager.nodes.pop(conn.name, None)
            registry.remove_remote(conn.name)
            print(f"Node left: {conn.name}")


# ─── File exchange (core shared folder <-> node shared folders) ──
def safe_name(name):
    """Filename only — no paths, no traversal."""
    import os as _os
    n = str(name or "").strip()
    if not n or n != _os.path.basename(n) or n.startswith(".") or ".." in n:
        return None
    return n


def _token_ok(request: Request):
    return hmac.compare_digest(request.headers.get("x-lucy-token", ""), get_node_token())


@router.get("/node/files/{name}")
async def node_file_get(name: str, request: Request):
    if not _token_ok(request):
        return PlainTextResponse("forbidden", status_code=403)
    n = safe_name(name)
    path = config.SHARED_DIR / n if n else None
    if not path or not path.is_file():
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(str(path), filename=n)


@router.post("/node/files/upload")
async def node_file_upload(request: Request, name: str):
    if not _token_ok(request):
        return PlainTextResponse("forbidden", status_code=403)
    n = safe_name(name)
    if not n:
        return PlainTextResponse("bad name", status_code=400)
    body = await request.body()
    if len(body) > config.MAX_FILE_MB * 1024 * 1024:
        return PlainTextResponse("too large", status_code=413)
    config.SHARED_DIR.mkdir(parents=True, exist_ok=True)
    (config.SHARED_DIR / n).write_bytes(body)
    return {"ok": True, "name": n, "bytes": len(body)}


# ─── Bootstrap ────────────────────────────────────────────
@router.get("/node/agent.py")
async def agent_source():
    return FileResponse(str(config.ROOT / "lucy" / "node" / "node.py"),
                        media_type="text/x-python")


@router.get("/node/bootstrap.ps1")
async def bootstrap(request: Request):
    core = str(request.base_url).rstrip("/")
    script = f"""$ErrorActionPreference = 'Stop'
Write-Host ''
Write-Host '=== Lucy Node setup ===' -ForegroundColor Magenta
$dir = "$env:USERPROFILE\\LucyNode"
New-Item -ItemType Directory -Force $dir | Out-Null

function Refresh-Path {{
  $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
              [Environment]::GetEnvironmentVariable('Path','User')
}}

# -- 1. Python ---------------------------------------------------------
$py = $null
foreach ($cand in @('py','python')) {{
  if (Get-Command $cand -ErrorAction SilentlyContinue) {{ $py = $cand; break }}
}}
if (-not $py) {{
  Write-Host 'Python not found - installing via winget...' -ForegroundColor Yellow
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {{
    Write-Host 'winget missing too. Install Python manually from python.org, then rerun this line.' -ForegroundColor Red
    exit 1
  }}
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  Refresh-Path
  $py = if (Get-Command py -ErrorAction SilentlyContinue) {{ 'py' }} else {{ 'python' }}
}}
Write-Host "Python: $py" -ForegroundColor Green

# -- 2. Agent + config -------------------------------------------------
Invoke-RestMethod "{core}/node/agent.py" -OutFile "$dir\\node.py"
if (-not $env:LUCY_TOKEN) {{ $env:LUCY_TOKEN = Read-Host 'Lucy node token' }}
$cfg = @{{ core_url = '{core}'; token = $env:LUCY_TOKEN; name = $env:COMPUTERNAME }}
$cfg | ConvertTo-Json | Set-Content "$dir\\config.json" -Encoding utf8

# -- 3. Python deps ----------------------------------------------------
Write-Host 'Installing python packages (websockets, psutil, zeroconf)...'
& $py -m pip install --quiet --upgrade websockets psutil zeroconf

# -- 4. Optional: Ollama + lightweight LLM -----------------------------
$wantLLM = if ($env:LUCY_LLM) {{ $env:LUCY_LLM }} else {{ Read-Host 'Install Ollama + a small local LLM on this device? (y/N)' }}
if ($wantLLM -match '^[1Yy]') {{
  if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {{
    Write-Host 'Installing Ollama...' -ForegroundColor Yellow
    winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
    Refresh-Path
  }}
  try {{ Start-Process ollama -ArgumentList 'serve' -WindowStyle Hidden }} catch {{}}
  Start-Sleep -Seconds 4
  Write-Host 'Pulling lightweight model (qwen2.5:1.5b, ~1GB)...'
  & ollama pull qwen2.5:1.5b
}}

# -- 5. Optional: auto-start at login ----------------------------------
$wantBoot = if ($env:LUCY_AUTOSTART) {{ $env:LUCY_AUTOSTART }} else {{ Read-Host 'Start the Lucy node automatically at login? (y/N)' }}
if ($wantBoot -match '^[1Yy]') {{
  $startup = [Environment]::GetFolderPath('Startup')
  "start `"LucyNode`" /min $py `"$dir\\node.py`"" | Set-Content "$startup\\LucyNode.bat" -Encoding ascii
  Write-Host "Autostart entry created: $startup\\LucyNode.bat" -ForegroundColor Green
}}

Write-Host ''
Write-Host 'Lucy node starting - leave this window open.' -ForegroundColor Magenta
& $py "$dir\\node.py"
"""
    return PlainTextResponse(script, media_type="text/plain")
