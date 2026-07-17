"""Lucy Node agent — standalone, no lucy package imports.

Runs on any device. Self-probes hardware, reports capabilities to the Core,
heartbeats, executes dispatched tasks. Deps: pip install websockets psutil

Config: config.json next to this file:
    {"core_url": "http://192.168.1.10:8000", "token": "…", "name": "VIVOBOOK"}
"""
import asyncio, json, os, platform, socket, subprocess, sys, time
from pathlib import Path

try:
    import websockets
    import psutil
except ImportError:
    print("missing deps - run:  pip install websockets psutil")
    sys.exit(1)

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
PROTOCOL_VERSION = 1
AGENT_VERSION = "0.4.0"

SHARED = HERE / "shared"   # this device's Lucy drop folder
SHARED.mkdir(exist_ok=True)

# Named destinations a transfer can be aimed at — kept in sync with
# lucy/core/app.py's FOLDER_ALIASES so the same words work on both ends.
DEST_ALIASES = {
    "shared":    SHARED,
    "desktop":   Path.home() / "Desktop",
    "downloads": Path.home() / "Downloads",
    "documents": Path.home() / "Documents",
    "pictures":  Path.home() / "Pictures",
}


def resolve_dest(alias):
    path = DEST_ALIASES.get(str(alias or "shared").strip().lower(), SHARED)
    path.mkdir(parents=True, exist_ok=True)
    return path

# idempotency cache: key -> (timestamp, result). Retried tasks return the
# cached result instead of re-executing (matters once actions have side effects)
IDEM_TTL_S = 300
_idem_cache = {}


def _ver(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except Exception:
        return (0,)


# ─── mDNS discovery fallback ──────────────────────────────
def discover_core(timeout=6):
    """Find the Lucy core on the LAN when the configured URL is unreachable
    (e.g. the core machine got a new DHCP address)."""
    try:
        from zeroconf import Zeroconf, ServiceBrowser
    except ImportError:
        return None
    found = {}

    class Listener:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name)
            if info and info.addresses:
                ip = socket.inet_ntoa(info.addresses[0])
                found["url"] = f"http://{ip}:{info.port}"
        def update_service(self, *a): pass
        def remove_service(self, *a): pass

    zc = Zeroconf()
    ServiceBrowser(zc, "_lucy._tcp.local.", Listener())
    t0 = time.time()
    while "url" not in found and time.time() - t0 < timeout:
        time.sleep(0.25)
    zc.close()
    return found.get("url")


# ─── Self-update ──────────────────────────────────────────
def self_update(core_url):
    import urllib.request
    me = HERE / "node.py"
    print("downloading agent update...")
    with urllib.request.urlopen(f"{core_url}/node/agent.py", timeout=15) as r:
        new_src = r.read()
    (HERE / "node.py.bak").write_bytes(me.read_bytes())
    tmp = HERE / "node.py.new"
    tmp.write_bytes(new_src)
    os.replace(tmp, me)
    print("agent updated - restarting")
    os.execv(sys.executable, [sys.executable, str(me)])


# ─── Self-probe ───────────────────────────────────────────
def probe_gpu():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            name, mem = out.stdout.strip().splitlines()[0].split(",", 1)
            return {"kind": "cuda", "name": name.strip(), "vram": mem.strip()}
    except Exception:
        pass
    try:
        out = subprocess.run(["wmic", "path", "win32_VideoController", "get", "name"],
                             capture_output=True, text=True, timeout=5)
        names = [l.strip() for l in out.stdout.splitlines()[1:] if l.strip()]
        if names:
            return {"kind": "generic", "name": names[0]}
    except Exception:
        pass
    return None


def probe_ollama():
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
            models = [m["name"] for m in json.load(r).get("models", [])]
        return models
    except Exception:
        return None


def build_capabilities():
    caps = [{
        "capability": "monitoring.system",
        "provider": "psutil",
        "actions": ["read_stats"],
        "version": "0.1.0",
    }, {
        "capability": "files.transfer",
        "provider": "lucy-node",
        "actions": ["list", "deliver", "fetch"],
        "version": "0.3.0",
        "meta": {"folder": str(SHARED), "dest_aliases": sorted(DEST_ALIASES)},
    }]
    gpu = probe_gpu()
    if gpu and gpu["kind"] == "cuda":
        caps.append({"capability": "compute.cuda", "provider": "nvidia",
                     "actions": [], "meta": gpu, "version": "0.1.0"})
    models = probe_ollama()
    if models:
        caps.append({"capability": "llm.local", "provider": "ollama",
                     "actions": ["chat"], "meta": {"models": models}, "version": "0.1.0"})
    return caps


# ─── Built-in plugin: monitoring.system ───────────────────
def read_stats():
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    batt = psutil.sensors_battery()
    stats = {
        "cpu_percent": psutil.cpu_percent(interval=0.3),
        "ram_used_gb": round(vm.used / 1e9, 1),
        "ram_total_gb": round(vm.total / 1e9, 1),
        "ram_percent": vm.percent,
        "disk_percent": disk.percent,
        "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 1),
    }
    if batt:
        stats["battery_percent"] = batt.percent
        stats["on_ac"] = batt.power_plugged
    gpu = probe_gpu()
    if gpu and gpu["kind"] == "cuda":
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,temperature.gpu",
                 "--format=csv,noheader"], capture_output=True, text=True, timeout=5)
            util, mem, temp = out.stdout.strip().split(",")
            stats["gpu"] = {"util": util.strip(), "vram_used": mem.strip(),
                            "temp_c": temp.strip(), "name": gpu["name"]}
        except Exception:
            pass
    return stats


# ─── Built-in plugin: files.transfer ──────────────────────
def _safe_file(name):
    import os.path
    n = str(name or "").strip()
    if not n or n != os.path.basename(n) or n.startswith(".") or ".." in n:
        raise ValueError(f"unsafe filename: {name!r}")
    return n


def files_list():
    return [{"name": f.name, "bytes": f.stat().st_size}
            for f in sorted(SHARED.iterdir()) if f.is_file()]


def _progress_bar(pct):
    filled = pct // 5
    return "#" * filled + "-" * (20 - filled)


def files_pull(name, dest_alias="shared", on_progress=None):
    """Download a file from the core's shared folder into this device's,
    reporting live progress (console bar + on_progress callback for the
    core/browser to display) as it streams in."""
    import urllib.request, urllib.parse
    n = _safe_file(name)
    url = f"{CONFIG['core_url']}/node/files/{urllib.parse.quote(n)}"
    req = urllib.request.Request(url, headers={"X-Lucy-Token": CONFIG["token"]})
    dest_dir = resolve_dest(dest_alias)
    dest_path = dest_dir / n

    with urllib.request.urlopen(req, timeout=120) as r:
        total = int(r.headers.get("Content-Length") or 0)
        print(f"\n[incoming] '{n}' ({total/1e6:.1f} MB) from core -> {dest_dir}")
        done = 0
        last_pct = -1
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
        with open(tmp_path, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                pct = int(done * 100 / total) if total else 100
                if pct != last_pct:
                    last_pct = pct
                    print(f"  [{_progress_bar(pct)}] {pct}%", end="\r", flush=True)
                    if on_progress and (pct % 10 == 0 or pct == 100):
                        on_progress(done, total)
        os.replace(tmp_path, dest_path)
    print(f"\n[done] saved to {dest_path}")
    return {"saved": n, "bytes": dest_path.stat().st_size,
            "folder": str(dest_dir), "path": str(dest_path)}


def files_push(name):
    """Upload a file from this device's shared folder to the core's."""
    import urllib.request, urllib.parse
    n = _safe_file(name)
    path = SHARED / n
    if not path.is_file():
        raise FileNotFoundError(f"'{n}' is not in this device's shared folder")
    data = path.read_bytes()
    url = f"{CONFIG['core_url']}/node/files/upload?name={urllib.parse.quote(n)}"
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"X-Lucy-Token": CONFIG["token"]})
    with urllib.request.urlopen(req, timeout=120) as r:
        r.read()
    return {"sent": n, "bytes": len(data)}


async def run_task(capability, action, params, idem=None, ws=None, task_id=None):
    # idempotency: a retried task returns its cached result
    if idem:
        hit = _idem_cache.get(idem)
        if hit and (time.time() - hit[0]) < IDEM_TTL_S:
            return hit[1]

    if capability == "monitoring.system" and action == "read_stats":
        result = await asyncio.to_thread(read_stats)
    elif capability == "files.transfer" and action == "list":
        result = await asyncio.to_thread(files_list)
    elif capability == "files.transfer" and action == "deliver":
        loop = asyncio.get_running_loop()

        def on_progress(done, total):
            if ws and task_id:
                asyncio.run_coroutine_threadsafe(
                    _send(ws, {"v": 1, "type": "task_progress", "task_id": task_id,
                              "done": done, "total": total}), loop)

        result = await asyncio.to_thread(
            files_pull, params["name"], params.get("dest", "shared"), on_progress)
    elif capability == "files.transfer" and action == "fetch":
        result = await asyncio.to_thread(files_push, params["name"])   # this device -> core
    else:
        raise ValueError(f"unsupported task: {capability}.{action}")
    if idem:
        _idem_cache[idem] = (time.time(), result)
        if len(_idem_cache) > 100:
            _idem_cache.pop(next(iter(_idem_cache)))
    return result


async def _send(ws, obj):
    try:
        await ws.send(json.dumps(obj))
    except Exception:
        pass  # connection may already be closing — progress updates are best-effort


# ─── Heartbeat payload ────────────────────────────────────
def heartbeat_stats():
    batt = psutil.sensors_battery()
    s = {"cpu": psutil.cpu_percent(interval=None),
         "ram": psutil.virtual_memory().percent}
    if batt:
        s["battery"] = batt.percent
        s["on_ac"] = batt.power_plugged
    return s


# ─── Main loop ────────────────────────────────────────────
async def session(core_url):
    ws_url = core_url.replace("http://", "ws://").replace("https://", "wss://") + "/node"
    async with websockets.connect(ws_url, max_size=4 * 1024 * 1024) as ws:
        await ws.send(json.dumps({
            "v": PROTOCOL_VERSION, "type": "hello",
            "token": CONFIG["token"],
            "node": CONFIG.get("name") or platform.node(),
            "os": f"{platform.system()} {platform.release()}",
            "arch": platform.machine(),
            "agent_version": AGENT_VERSION,
            "capabilities": build_capabilities(),
        }))
        welcome = json.loads(await ws.recv())
        if welcome.get("type") != "welcome":
            print("auth failed - check the token in config.json")
            return False
        print(f"connected to core as '{welcome['name']}' (agent v{AGENT_VERSION})")

        latest = welcome.get("agent_version")
        if latest and _ver(latest) > _ver(AGENT_VERSION):
            print(f"core offers agent v{latest} (running v{AGENT_VERSION})")
            self_update(core_url)  # does not return — restarts the process

        async def heartbeats():
            while True:
                await ws.send(json.dumps({"v": 1, "type": "heartbeat",
                                          "stats": heartbeat_stats()}))
                await asyncio.sleep(5)

        hb = asyncio.create_task(heartbeats())
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "task":
                    tid = msg["task_id"]
                    try:
                        data = await asyncio.wait_for(
                            run_task(msg["capability"], msg["action"], msg.get("params") or {},
                                     idem=msg.get("idempotency_key"), ws=ws, task_id=tid),
                            timeout=msg.get("timeout_s", 30))
                        await ws.send(json.dumps({"v": 1, "type": "result",
                                                  "task_id": tid, "ok": True, "data": data}))
                    except Exception as e:
                        await ws.send(json.dumps({"v": 1, "type": "result",
                                                  "task_id": tid, "ok": False, "error": repr(e)}))
                elif msg.get("type") == "cancel":
                    pass  # cooperative cancel lands with long-running tasks
        finally:
            hb.cancel()
    return True


async def main():
    core_url = CONFIG["core_url"]
    print(f"Lucy Node v{AGENT_VERSION} - core: {core_url}")
    fails = 0
    while True:
        try:
            ok = await session(core_url)
            if ok is False:
                return
            fails = 0
        except Exception as e:
            fails += 1
            print(f"connection lost ({e}) - retrying in 5s")
            # after two straight failures, ask the LAN where the core went
            if fails >= 2:
                found = await asyncio.to_thread(discover_core)
                if found and found != core_url:
                    print(f"mDNS found core at {found}")
                    core_url = found
                    CONFIG["core_url"] = found
                    try:
                        CONFIG_PATH.write_text(json.dumps(CONFIG, indent=2), encoding="utf-8")
                    except Exception:
                        pass
                    fails = 0
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
