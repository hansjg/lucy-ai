"""Lucy Core — conversation manager and web server.

Phase 0: same behavior as the old monolithic main.py, but every capability
(STT, TTS, LLM, wake word) is now a plugin routed through the registry.
"""
import gc, re, json, base64, asyncio, shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

from . import config, memory, db, scheduler, planner, settings, people
from .registry import registry
from .nodes import (router as node_router, node_manager, print_pairing_line,
                    register_mdns, unregister_mdns, safe_name,
                    known_nodes, send_wol, get_node_token, lan_ip,
                    set_nickname, device_label)
from lucy.shared.text import clean_for_tts

# ─── Connected clients ────────────────────────────────────
connected_clients: list[WebSocket] = []


async def _broadcast(msg: dict):
    for ws in list(connected_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def _broadcast_wake(_data=None):
    print(f"Broadcasting wake to {len(connected_clients)} clients")
    await _broadcast({"type": "wake"})


async def _broadcast_progress(data):
    await _broadcast({"type": "task_progress", **(data or {})})

registry.on("wake", _broadcast_wake)
registry.on("task_progress", _broadcast_progress)


async def say(ws, text, expect_answer=False):
    """Send a Lucy reply that isn't LLM-generated — used for deterministic
    confirmations and wizard prompts where correctness matters more than
    natural phrasing (a small local model WILL invent details if asked to
    narrate something this factual).

    expect_answer marks the reply as a QUESTION. The browser reopens the mic
    for it when the user came in by voice, so answering a follow-up doesn't
    need another "hey lucy". Typed users are left alone."""
    await ws.send_json({"type": "reply_start"})
    await ws.send_json({"type": "token", "text": text})
    await ws.send_json({"type": "reply_end", "expect_answer": expect_answer})
    await ws.send_json({"type": "status", "state": "idle"})


# ─── App ──────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    print("-- Lucy Core : plugin runtime --")
    db.init()
    await registry.start_all()
    await register_mdns()
    print_pairing_line()
    print(f"Ready - visit http://localhost:{config.PORT}")
    yield
    unregister_mdns()

app = FastAPI(lifespan=lifespan)
app.include_router(node_router)
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


@app.get("/")
async def index():
    # index.html carries the ?v= cache-busters for app.js/style.css — so if
    # the browser caches THIS, every future UI update is invisible and no
    # amount of bumping the version helps. It must always be revalidated.
    return FileResponse(str(config.STATIC_DIR / "index.html"),
                        headers={"Cache-Control": "no-cache, must-revalidate"})


# ─── Observability API (task log panel) ───────────────────
@app.get("/api/nodes")
async def api_nodes():
    core = {
        "name": "core",
        "label": "core",
        "kind": "local",
        "alive": True,
        "os": "this machine",
        "capabilities": sorted(registry.manifests.keys()),
        "stats": {},
    }
    remote = node_manager.snapshot()
    for n in remote:
        n["label"] = device_label(n["name"])   # nicknames win on every display
    return {"nodes": [core] + remote}


@app.get("/api/tasks")
async def api_tasks():
    return {"tasks": db.recent_tasks(30)}


# ─── Shared files (the ecosystem drop folder) ─────────────
@app.get("/api/files")
async def api_files():
    config.SHARED_DIR.mkdir(parents=True, exist_ok=True)
    files = [{"name": f.name, "bytes": f.stat().st_size}
             for f in sorted(config.SHARED_DIR.iterdir()) if f.is_file()]
    return {"files": files}


@app.post("/api/files/upload")
async def api_files_upload(request: Request, name: str):
    n = safe_name(name)
    if not n:
        return {"ok": False, "error": "bad filename"}
    body = await request.body()
    if len(body) > config.MAX_FILE_MB * 1024 * 1024:
        return {"ok": False, "error": f"file exceeds {config.MAX_FILE_MB}MB"}
    config.SHARED_DIR.mkdir(parents=True, exist_ok=True)
    (config.SHARED_DIR / n).write_bytes(body)
    return {"ok": True, "name": n, "bytes": len(body)}


# ─── Settings & device pairing info ───────────────────────
def _device_inventory():
    """Every device Lucy has ever seen, newest first, with wake readiness."""
    live = {n["name"] for n in node_manager.snapshot() if n["alive"]}
    known = known_nodes()
    devices = []
    for n in db.all_nodes():
        info = known.get(n["name"], {})
        devices.append({
            "name": n["name"],
            "label": info.get("nickname") or n["name"],
            "online": n["name"] in live,
            "wakeable": bool(info.get("mac")),
        })
    return devices


def _people_inventory():
    """Who Lucy knows, with the pre-approvals each has granted. The topic
    itself is never included here — the UI asks for it separately."""
    return [{"name": p["name"], "space": p["space"],
             "auto_allow": p.get("auto_allow", [])} for p in people.load()]


@app.get("/api/settings")
async def api_settings_get():
    return {"settings": settings.load(), "devices": _device_inventory(),
            "people": _people_inventory()}


@app.post("/api/settings")
async def api_settings_post(request: Request):
    body = await request.json()
    # "make me a topic" — the UI must never invent one; a short or guessable
    # topic is the whole security of an ntfy push.
    if body.pop("new_topic", False):
        body["ntfy_topic"] = settings.new_ntfy_topic()
    return {"settings": settings.save(body), "devices": _device_inventory(),
            "people": _people_inventory()}


@app.post("/api/people/revoke")
async def api_people_revoke(request: Request):
    body = await request.json()
    n = people.revoke_auto(body.get("owner", ""), body.get("requester"))
    return {"ok": True, "revoked": n, "people": _people_inventory()}


@app.get("/api/pairing")
async def api_pairing(response: Response):
    # The token rotates. A cached response here hands out a DEAD command and
    # the device fails with a bare "auth failed" for no visible reason — so
    # this endpoint must never be cached by the browser.
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    return {"command": f"$env:LUCY_TOKEN='{get_node_token()}'; "
                       f"irm http://{lan_ip()}:{config.PORT}/node/bootstrap.ps1 | iex"}


# ─── Real-time vision (camera detection loop) ─────────────
# The browser sends one frame at a time and waits for the boxes before
# sending the next, so a slow GPU self-paces instead of piling frames up.
@app.post("/api/vision/detect")
async def api_vision_detect(request: Request):
    if not registry.has("vision.locate"):
        return {"ok": False, "state": "missing",
                "error": "vision.locate plugin didn't start (check server log)"}
    body = await request.json()
    try:
        res = await registry.call("vision.locate", "detect",
                                  image_b64=body.get("image", ""),
                                  query=body.get("query") or None)
        return {"ok": res.get("state") == "ready", **res}
    except Exception as e:
        return {"ok": False, "state": "error", "error": str(e)[:300]}


@app.get("/api/vision/status")
async def api_vision_status():
    if not registry.has("vision.locate"):
        return {"state": "missing"}
    return await registry.call("vision.locate", "status")


@app.post("/api/vision/release")
async def api_vision_release():
    if not registry.has("vision.locate"):
        return {"state": "missing"}
    return await registry.call("vision.locate", "release")


# ─── Chat slash commands (manual routing, Phase 1) ────────
async def handle_command(ws, text):
    parts = text.split()
    cmd = parts[0].lower()

    if cmd == "/nodes":
        lines = []
        for n in (await api_nodes())["nodes"]:
            state = "online" if n["alive"] else "away"
            stats = n.get("stats") or {}
            extra = f" cpu {stats.get('cpu')}%" if "cpu" in stats else ""
            batt = f" batt {stats.get('battery')}%" if "battery" in stats else ""
            shown = n.get("label") or n["name"]
            if shown != n["name"]:
                shown = f"{shown} ({n['name']})"   # /nodes is the spec view
            lines.append(f"{shown} ({state}){extra}{batt} - {', '.join(n['capabilities'])}")
        await say(ws, "\n".join(lines) if lines else "no nodes connected")

    elif cmd == "/task" and len(parts) >= 3:
        capability, action = parts[1], parts[2]
        pin, params = None, {}
        for extra in parts[3:]:
            if extra.startswith("{"):
                params = json.loads(text[text.index(extra):])
                break
            pin = extra
        res = await scheduler.execute_task(registry, capability, action, params, pin)
        if res["ok"]:
            body = json.dumps(res["data"], indent=2, default=str)
            await say(ws, f"[{res['node']} - {res['ms']}ms]\n{body}")
        else:
            await say(ws, f"task failed: {res['error']}")

    else:
        await say(ws, "commands: /nodes | /task <capability> <action> [node] [json-params]")


# ─── Device directory (real names + nicknames) ─────────────
def _norm_dev(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def device_directory():
    """Every device Lucy has ever met, newest first: real name + display label."""
    return [{"name": n["name"], "label": device_label(n["name"])}
            for n in db.all_nodes()]


# words that describe a device without naming one
_DEVICE_STOPWORDS = {"the", "one", "that", "this", "it", "pick", "my", "your",
                     "other", "laptop", "notebook", "device", "pc", "computer",
                     "node", "machine", "please", "lucy"}


def find_device(text):
    """Real name of a device mentioned in free text, by name or nickname."""
    t = _norm_dev(text)
    for d in device_directory():
        if _norm_dev(d["label"]) in t or _norm_dev(d["name"]) in t:
            return d["name"]
    return None


def match_device_answer(text, options):
    """Resolve a which-device answer: '2', 'the vivobook', 'laptop q0c'."""
    i = parse_index(text, len(options))
    if i is not None:
        return options[i]
    t = _norm_dev(text)
    for name in options:
        for cand in (name, device_label(name)):
            c = _norm_dev(cand)
            if c and (c in t or t in c):
                return name
    # partial-name chunks: 'laptop q0c' -> 'q0c' matches LAPTOP-Q0C6AQGS
    chunks = [c for c in re.split(r"[^a-z0-9]+", text.lower())
              if len(c) >= 2 and c not in _DEVICE_STOPWORDS]
    for name in options:
        hay = _norm_dev(name) + " " + _norm_dev(device_label(name))
        if chunks and all(c in hay for c in chunks):
            return name
    return None


# ─── "Allow Sam" / "deny Sam" — the owner's answer ─────────
# Never routed through the LLM: a model that improvises "sure, allowed!" would
# be inventing consent. Only the real owner's words resolve a real request.
CONSENT_RE = re.compile(r"\b(allow|approve|let|yes to|deny|refuse|block|no to)\b", re.I)


def looks_like_consent_reply(text, speaker):
    return bool(speaker and profiles_on() and CONSENT_RE.search(text)
                and people.pending_for(speaker))


async def handle_consent_reply(ws, text, speaker):
    waiting = people.pending_for(speaker)
    if not waiting:
        return False
    t = text.lower()
    allow = not re.search(r"\b(deny|refuse|block|no to)\b", t)

    # Match by requester name when several people are waiting; a bare "yes"
    # only works when there's exactly one thing it could mean.
    req = next((r for r in waiting if r["requester"].lower() in t), None)
    if req is None:
        if len(waiting) > 1:
            who = ", ".join(f"{r['requester']} ('{r['resource']}')" for r in waiting)
            await say(ws, f"Which one? {who}", expect_answer=True)
            return True
        req = waiting[0]

    people.resolve(req["id"], allow)
    if not allow:
        await say(ws, f"Okay — I told {req['requester']} no.")
        return True

    always = bool(re.search(r"\b(always|from now on|don'?t ask again)\b", t))
    extra = ""
    if always:
        until = people.grant_auto(speaker, req["requester"], hours=24)
        extra = (f" I won't ask again for {req['requester']} until "
                 f"{until[11:16]} tomorrow — say 'stop allowing "
                 f"{req['requester']}' to end it sooner.")
    await say(ws, f"Done — {req['requester']} can have '{req['resource']}'.{extra}")
    return True


# ─── "Rename that device" — nicknames, deterministic ───────
# The LLM happily claims "Okay, I've renamed it" and does nothing. This
# intercept stores a real nickname: it replaces the device's displayed name
# everywhere, while the original name stays saved and comes up in specs.
RENAME_INTENT_RE = re.compile(
    r"\b(rename|nickname|nick-?name)\b|\bcall\s+(it|that|this|the)\b", re.I)
NICKNAME_RE = re.compile(
    r"\b(?:as|to|into|named|called)\s+[\"']?([A-Za-z][\w\-]{0,23})[\"']?", re.I)


def looks_like_rename_intent(text):
    if not RENAME_INTENT_RE.search(text):
        return False
    # only intercept when it's about a device we know (or clearly device-ish)
    return bool(find_device(text)) or bool(re.search(
        r"\b(laptop|notebook|device|pc|computer|node|machine)\b", text, re.I))


async def handle_rename_request(ws, text):
    target = find_device(text)
    m = NICKNAME_RE.search(text)
    if not target:
        await say(ws, "Which device? Tell me like this: rename LAPTOP-Q0C6AQGS to Vivobook.")
        return
    if not m:
        await say(ws, f"What should I call {device_label(target)}? Say: rename "
                      f"{device_label(target)} to — and the new name.")
        return
    nick = m.group(1).strip()
    old_label = device_label(target)
    set_nickname(target, nick)
    real = f" Its real name, {target}, stays saved for the spec sheet." \
        if nick.lower() != target.lower() else ""
    await say(ws, f"Done — {old_label} goes by {nick} now.{real}")


# ─── "Connect my laptop" — deterministic device wake ───────
# The LLM used to answer "Connecting now…" to this and do nothing. Nodes
# join inbound, so the only real remote action the core has is Wake-on-LAN
# to a device it has seen before — gated behind the "Connect devices
# through Lucy" setting, with an honest follow-up either way.
CONNECT_INTENT_RE = re.compile(
    r"\b(connect|re-?connect|pair|link\s+up|wake(\s+up)?|turn\s+on|bring)\b"
    r"[^.?!]{0,50}\b(laptop|notebook|device|pc|computer|node|machine)s?\b",
    re.I)


def looks_like_connect_intent(text):
    # "connect my vivobook" names the device instead of a device-word
    return bool(CONNECT_INTENT_RE.search(text)) or bool(
        re.search(r"\b(connect|re-?connect|wake(\s+up)?|turn\s+on)\b", text, re.I)
        and find_device(text))


async def _watch_for_join(ws, target):
    """After a wake signal: report when the device joins, or admit it didn't."""
    label = device_label(target)
    for _ in range(max(config.WAKE_WAIT_S // 3, 1)):
        await asyncio.sleep(3)
        if target in await live_node_names():
            if ws in connected_clients:
                await say(ws, f"{label} just came online — you're connected.")
            return
    if ws in connected_clients:
        await say(ws, f"{label} hasn't answered the wake-up call. It might be fully "
                      f"powered off, on a different network, or have wake-on-LAN "
                      f"disabled. Starting the Lucy node on it by hand will connect it.")


async def wake_device(ws, target):
    """The actual wake: known MAC -> WoL + join watcher, honest otherwise."""
    label = device_label(target)
    info = known_nodes().get(target, {})
    if not info.get("mac"):
        await say(ws, f"I know {label}, but I never learned its network address, so "
                      f"I can't wake it remotely yet. Connect it once more while it's "
                      f"on — I'll remember it from then on.")
        return
    try:
        await asyncio.to_thread(send_wol, info["mac"])
    except Exception as e:
        await say(ws, f"I tried to send the wake-up signal to {label} but it failed: {e}")
        return
    await say(ws, f"Sent a wake-up signal to {label}. If it allows wake-on-LAN it "
                  f"should be online within half a minute — I'll tell you when I see it.")
    asyncio.get_running_loop().create_task(_watch_for_join(ws, target))


async def handle_connect_request(ws, text):
    """Returns a pending which-device state, or None when handled."""
    cfg = settings.load()
    devices = device_directory()
    live = await live_node_names()

    # a device named in the sentence beats the configured default
    target = find_device(text) or cfg.get("connect_target") or (
        devices[0]["name"] if len(devices) == 1 else None)

    if target and target in live:
        await say(ws, f"{device_label(target)} is already connected — it's online right now.")
        return None

    if not cfg.get("connect_devices"):
        await say(ws, "I can't reach out to devices on my own yet — that switch is off. "
                      "Open the settings button up top and turn on 'Connect devices "
                      "through Lucy'. Then ask me again.")
        return None

    if not devices:
        await say(ws, "I haven't met any of your devices yet. Pair the laptop once "
                      "with the command in the nodes panel — after that I can wake "
                      "it myself.")
        return None

    if not target:
        opts = ", ".join(f"{i+1}. {d['label']}" for i, d in enumerate(devices))
        await say(ws, f"Which device do you mean? {opts} — say the number or the name.",
                  expect_answer=True)
        return {"stage": "await_device", "options": [d["name"] for d in devices]}

    await wake_device(ws, target)
    return None


async def advance_connect_choice(ws, state, text):
    """The follow-up answer to 'which device?' — 'laptop q0c' must just work."""
    bail = wizard_bailout(text)
    if bail == "cancel":
        await say(ws, "Okay, dropped it.")
        return None
    if bail == "pass":
        return "PASS"
    name = match_device_answer(text, state["options"])
    if not name:
        await say(ws, "Didn't catch which one — say the number or the name, or 'cancel'.")
        return state
    if name in await live_node_names():
        await say(ws, f"{device_label(name)} is already connected and online.")
        return None
    await wake_device(ws, name)
    return None


# ─── Guided file-share wizard ──────────────────────────────
# Used when the user references a file without naming one explicitly
# ("send this to my other device") — deterministic slot-filling, no LLM,
# because we've already been bitten twice by a 3B model inventing details.
FOLDER_ALIASES_CORE = {
    "shared":    config.SHARED_DIR,
    "desktop":   Path.home() / "Desktop",
    "downloads": Path.home() / "Downloads",
    "documents": Path.home() / "Documents",
    "pictures":  Path.home() / "Pictures",
}

# "remember my voice, I'm Hans" / "remember my voice. my name is Hans"
ENROLL_VOICE_RE = re.compile(
    r"remember\s+my\s+voice\b[^A-Za-z]*(?:i'?\s?a?m|my\s+name\s+is|it'?s|this\s+is)?\s*"
    r"(?P<name>[A-Za-z][A-Za-z\- ]{1,24})", re.I)

SHARE_INTENT_RE = re.compile(
    r"\b(send|share|transfer|copy|move)\b.{0,40}\b(device|laptop|vivobook|node|other|pc|computer)\b"
    r"|\b(send|share|transfer|upload)\b.{0,25}\bfiles?\b"
    r"|\b(send|share)\s+(this|it|that|something)\b", re.I)

# a token that looks like an actual filename, e.g. report.pdf, backup_codes.txt
FILENAME_RE = re.compile(r"\b[\w-]+\.[A-Za-z0-9]{1,8}\b")


def looks_like_share_intent(text):
    return bool(SHARE_INTENT_RE.search(text))


def names_a_file(text):
    return bool(FILENAME_RE.search(text))


def resolve_core_folder(text):
    key = text.strip().lower()
    if key in FOLDER_ALIASES_CORE:
        return FOLDER_ALIASES_CORE[key]
    p = Path(text.strip()).expanduser()
    return p if p.is_dir() else None


def recent_files(folder, n=3):
    try:
        files = [f for f in folder.iterdir() if f.is_file()]
    except Exception:
        return []
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"name": f.name, "path": str(f), "bytes": f.stat().st_size} for f in files[:n]]


ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
                 "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5}

CANCEL_WORDS = ("cancel", "nevermind", "never mind", "forget it", "abort", "stop it")


def parse_index(text, n):
    """'2', '2nd one', 'the second', 'last' -> 0-based index into n options."""
    t = text.strip().lower()
    m = re.search(r"\b(\d+)\b", t)
    if m:
        i = int(m.group(1))
        return i - 1 if 1 <= i <= n else None
    for word, i in ORDINAL_WORDS.items():
        if word in t:
            return i - 1 if i <= n else None
    if "last" in t:
        return n - 1
    return None


def wizard_bailout(text):
    """Detect that a reply isn't an answer to the wizard at all."""
    t = text.strip().lower()
    if any(w in t for w in CANCEL_WORDS):
        return "cancel"
    if t.endswith("?") or re.match(
            r"^(which|what|who|where|when|why|how|can|could|do|does|is|are|hey)\b", t):
        return "pass"
    return None


def pick_candidate(text, candidates):
    t = text.strip().lower()
    i = parse_index(text, len(candidates))
    if i is not None:
        return candidates[i]
    for c in candidates:
        if c["name"].lower() in t or t in c["name"].lower():
            return c
    return None


# "two of them", "both", "all of it", "the first three"
COUNT_WORDS = {"both": 2, "two": 2, "three": 3, "four": 4, "five": 5,
               "couple": 2, "pair": 2}


def wanted_count(text):
    """How many files the user asked for, if they said. None = unspecified."""
    t = text.lower()
    if re.search(r"\b(all|everything|every one|the lot)\b", t):
        return "all"
    for word, n in COUNT_WORDS.items():
        if re.search(rf"\b{word}\b", t):
            return n
    m = re.search(r"\b(\d+)\s+(?:of\s+)?(?:the\s+)?(?:files?|of\s+them|of\s+it)", t)
    if m:
        return int(m.group(1))
    return None


def pick_candidates(text, candidates):
    """Resolve a possibly-multi answer: 'all', 'both', '1 and 3', 'the pdf'."""
    t = text.strip().lower()
    if re.search(r"\b(all|both|everything|every one)\b", t):
        n = 2 if "both" in t else len(candidates)
        return candidates[:n]
    picked = []
    for num in re.findall(r"\b(\d+)\b", t):        # "1 and 3", "2, 4"
        i = int(num) - 1
        if 0 <= i < len(candidates) and candidates[i] not in picked:
            picked.append(candidates[i])
    if picked:
        return picked
    for c in candidates:                            # by name
        stem = c["name"].lower().rsplit(".", 1)[0]
        if c["name"].lower() in t or (len(stem) > 4 and stem in t):
            if c not in picked:
                picked.append(c)
    if picked:
        return picked
    one = pick_candidate(text, candidates)
    return [one] if one else []


def file_list_lines(files):
    return "\n".join(
        f"{i+1}. {f['name']} ({f['bytes']/1e6:.1f} MB)"
        + (f" — {f['owner']}'s" if f.get("owner") else "")
        for i, f in enumerate(files))


# ─── Whose files can this voice see? ──────────────────────
# Profiles mode off = Lucy exactly as she was: one flat pile, no filtering.
# On = you see your own space and the common pile, never anyone else's — a
# voice match picks YOUR space only, because a voice score is a similarity,
# not proof of who you are.
def profiles_on():
    return bool(settings.load().get("profiles_mode"))


def files_for_speaker(speaker):
    if not profiles_on():
        return recent_files(config.SHARED_DIR, 20)
    return people.visible_files(speaker)[:20]


def find_owned_file(text, speaker):
    """A file in SOMEONE ELSE's space that this text names, e.g. 'jeff's budget'.
    Returns (file, owner) or (None, None)."""
    if not profiles_on():
        return None, None
    t = _norm_dev(text)
    for f in people.all_private_files():
        if f["owner"] and speaker and f["owner"].lower() == str(speaker).lower():
            continue                     # your own space isn't "someone else's"
        stem = _norm_dev(f["name"].rsplit(".", 1)[0])
        if stem and len(stem) > 3 and stem in t:
            return f, f["owner"]
    return None, None


async def ask_owner(ws, requester, owner, file_entry):
    """Cross-space access: the owner decides, not the voice score.

    Auto-allow is honoured only when that person granted it to THIS requester
    and it hasn't expired — and even then it is announced, never silent.
    """
    label = file_entry["name"]
    if people.auto_allowed(owner, requester):
        await say(ws, f"{owner} pre-approved this for you, so here's '{label}'. "
                      f"I'll let {owner} know it was opened.")
        asyncio.get_running_loop().create_task(
            _notify_owner(owner, f"{requester} opened '{label}' (you pre-approved this)"))
        return True

    rid = people.request_access(requester, owner, label)
    await say(ws, f"That one's {owner}'s, so I've asked {owner} whether to share "
                  f"'{label}' with you. I'll only send it if they say yes.")
    asyncio.get_running_loop().create_task(
        _notify_owner(owner, f"{requester} is asking for '{label}'. "
                             f"Say 'allow {requester}' or 'deny {requester}'."))
    return rid


async def _notify_owner(owner, message):
    """Reach the owner wherever they are. Failing to reach them must never
    silently become a yes — the request just stays pending and expires."""
    if not registry.has("notify.push"):
        return
    try:
        await registry.call("notify.push", "push",
                            title=f"Lucy — {owner}", message=message, tag="lock")
    except Exception as e:
        print(f"[consent] couldn't reach {owner}: {e}")


def parse_dest_choice(text):
    t = text.strip().lower()
    for alias in ("desktop", "downloads", "documents", "pictures", "shared"):
        if alias in t:
            return alias
    return "shared"


def deliver_confirmation(name, node, folder_path, size_bytes):
    mb = size_bytes / 1e6
    return f"Done — '{name}' ({mb:.1f} MB) is now on {device_label(node)}, saved to {folder_path}"


async def live_node_names():
    return [n["name"] for n in node_manager.snapshot() if n["alive"]]


async def execute_share(ws, src_paths, display_name, node_name, dest_alias):
    """Copy the chosen file(s) into the core shared folder (if not already
    there) and dispatch each delivery, reporting a deterministic confirmation.
    Every file is reported by name — a summary like "sent 2 files" is exactly
    where a vague answer could hide a delivery that never happened."""
    if isinstance(src_paths, Path):
        src_paths = [src_paths]
    await ws.send_json({"type": "status", "state": "thinking"})

    sent, failed = [], []
    for src_path in src_paths:
        try:
            config.SHARED_DIR.mkdir(parents=True, exist_ok=True)
            target = config.SHARED_DIR / src_path.name
            if src_path.resolve() != target.resolve():
                shutil.copy2(src_path, target)
        except Exception as e:
            failed.append((src_path.name, str(e)))
            continue

        res = await scheduler.execute_task(registry, "files.transfer", "deliver",
                                           {"name": target.name, "dest": dest_alias}, node_name)
        if res["ok"]:
            data = res["data"]
            await ws.send_json({"type": "task_note", "task_id": res.get("task_id"),
                                "text": f"files.transfer.deliver → {res['node']} · {res['ms']}ms"})
            sent.append((data.get("saved", target.name), data.get("bytes", 0),
                         res["node"], data.get("folder", "the shared folder")))
        else:
            await ws.send_json({"type": "task_note", "task_id": res.get("task_id"),
                                "text": f"files.transfer.deliver failed · {res['error']}"})
            failed.append((src_path.name, res["error"]))

    if sent and not failed:
        if len(sent) == 1:
            name, size, node, folder = sent[0]
            await say(ws, deliver_confirmation(name, node, folder, size))
        else:
            names = ", ".join(f"'{n}'" for n, _, _, _ in sent)
            total = sum(b for _, b, _, _ in sent) / 1e6
            node, folder = sent[0][2], sent[0][3]
            await say(ws, f"Done — {len(sent)} files ({names}, {total:.1f} MB total) "
                          f"are now on {device_label(node)}, saved to {folder}")
    elif sent and failed:
        ok = ", ".join(f"'{n}'" for n, _, _, _ in sent)
        bad = "; ".join(f"'{n}' ({e})" for n, e in failed)
        await say(ws, f"Partly done — {ok} arrived on {device_label(node_name)}, "
                      f"but {bad} failed.")
    else:
        bad = "; ".join(f"'{n}' ({e})" for n, e in failed)
        await say(ws, f"Sending to {device_label(node_name)} failed: {bad}")


async def start_share_wizard(ws, context_file, text="", speaker=None):
    live = await live_node_names()
    want = wanted_count(text)          # "two of the files" / "all" / None

    # Asking for someone else's file is a consent question, not a file
    # question — settle that before the wizard offers anything.
    other, owner = find_owned_file(text, speaker)
    if other and owner:
        if not speaker:
            await say(ws, f"That's {owner}'s file, and I don't recognise your voice, "
                          f"so I can't ask on your behalf. Enrol first, or ask {owner}.")
            return None
        await ask_owner(ws, speaker, owner, other)
        return None

    have = files_for_speaker(speaker)

    # A file was just dropped AND the user asked for one file (or didn't say
    # a number) — the dropped one is unambiguous, use it.
    if context_file and not want:
        src = config.SHARED_DIR / context_file
        if len(live) == 1:
            await execute_share(ws, [src], context_file, live[0], "shared")
            return None
        if not live:
            await say(ws, f"Got '{context_file}' ready, but no devices are online right now.")
            return None
        opts = ", ".join(f"{i+1}. {device_label(n)}" for i, n in enumerate(live))
        await say(ws, f"Got it, '{context_file}' is ready — which device should I send it to? "
                      f"{opts} — say the number or the name.", expect_answer=True)
        return {"stage": "await_node", "files": [src], "display_name": context_file,
                "options": live}

    if not have:
        await say(ws, "I don't have any files yet — drop or paste them onto me first, "
                      "then say 'send this'. Or tell me which folder they're in.",
                  expect_answer=True)
        return {"stage": "await_folder"}

    # She knows exactly what she's holding — say so instead of guessing at the
    # newest one, which is how "send two of them" used to become "send that one".
    count = f"I've got {len(have)} file{'s' if len(have) != 1 else ''}"

    if want == "all":
        return await _confirm_files_then_node(ws, have, live)
    if isinstance(want, int):
        if want <= len(have):
            await say(ws, f"{count}. Which {want}? \n{file_list_lines(have)}\n"
                          f"Say the numbers — like '1 and 2'.", expect_answer=True)
            return {"stage": "await_file", "candidates": have}
        await say(ws, f"{count}, so I can't send {want}. Here's everything I have:\n"
                      f"{file_list_lines(have)}\nWhich ones?", expect_answer=True)
        return {"stage": "await_file", "candidates": have}

    if len(have) == 1:
        rf = have[0]
        await say(ws, f"Sure thing! Are you going to upload a new file, or did you mean "
                      f"'{rf['name']}' from before? Say 'upload' for a new one, "
                      f"or 'that one'.", expect_answer=True)
        return {"stage": "await_source_choice", "recent": rf}

    await say(ws, f"{count}. Which should I send?\n{file_list_lines(have)}\n"
                  f"Say the numbers, a name, or 'all'.", expect_answer=True)
    return {"stage": "await_file", "candidates": have}


async def _confirm_files_then_node(ws, files, live):
    """Files are settled — route to the device question (or just send)."""
    paths = [Path(f["path"]) for f in files]
    label = (files[0]["name"] if len(files) == 1
             else f"{len(files)} files")
    if not live:
        await say(ws, f"Got {label} ready, but no devices are online right now.")
        return None
    if len(live) == 1:
        return await confirm_and_maybe_ask_dest(
            ws, {"files": paths, "display_name": label}, live[0])
    opts = ", ".join(f"{i+1}. {device_label(n)}" for i, n in enumerate(live))
    await say(ws, f"Got it — {label}. Which device? {opts} — say the number or the name.",
              expect_answer=True)
    return {"stage": "await_node", "files": paths, "display_name": label, "options": live}


async def confirm_and_maybe_ask_dest(ws, state, node_name):
    await say(ws, f"Where should I put '{state['display_name']}' on {device_label(node_name)}? "
                  f"(Desktop, Downloads, Documents, Pictures, or say 'default')",
              expect_answer=True)
    return {**state, "stage": "await_dest", "node": node_name}


async def advance_share_wizard(ws, state, text):
    # Escape hatches: 'cancel' aborts; a question ("which devices are online?")
    # exits the wizard and lets the normal pipeline answer it instead of
    # trapping the user in a re-prompt loop.
    bail = wizard_bailout(text)
    if bail == "cancel":
        await say(ws, "Okay, cancelled — the file stays in the shared folder if you change your mind.")
        return None
    if bail == "pass":
        return "PASS"

    stage = state.get("stage")

    if stage == "await_source_choice":
        t = text.strip().lower()
        rf = state["recent"]
        if any(w in t for w in ("upload", "new one", "new file", "drop", "paste", "different", "another")):
            await say(ws, "Go ahead — drop or paste the file onto me, then say 'send this'.")
            return None
        folder = resolve_core_folder(text)
        if folder and folder.resolve() != config.SHARED_DIR.resolve():
            files = recent_files(folder)
            if not files:
                await say(ws, f"That folder's empty — try another, or say 'that one' for '{rf['name']}'.",
                          expect_answer=True)
                return state
            await say(ws, f"Here are the 3 most recent files there:\n{file_list_lines(files)}\n"
                          f"Which one — say the number or the name?", expect_answer=True)
            return {"stage": "await_file", "candidates": files}
        if (any(w in t for w in ("that", "yes", "yeah", "yep", "previous", "before", "same", "it"))
                or rf["name"].lower() in t or t == "1"):
            return await _confirm_files_then_node(ws, [rf], await live_node_names())
        await say(ws, f"Say 'upload' for a new file, or 'that one' to send '{rf['name']}'.",
                  expect_answer=True)
        return state

    if stage == "await_folder":
        folder = resolve_core_folder(text)
        if not folder:
            await say(ws, "I couldn't find that folder — try Desktop, Downloads, Documents, "
                          "Pictures, or a full path.", expect_answer=True)
            return state
        files = recent_files(folder)
        if not files:
            await say(ws, "That folder's empty — try another one?", expect_answer=True)
            return {"stage": "await_folder"}
        await say(ws, f"Here are the 3 most recent files there:\n{file_list_lines(files)}\n"
                      f"Which one — say the number or the name?", expect_answer=True)
        return {"stage": "await_file", "candidates": files}

    if stage == "await_file":
        chosen = pick_candidates(text, state["candidates"])
        if not chosen:
            await say(ws, "Didn't catch which ones — say the numbers (like '1 and 2'), "
                          "a filename, or 'all'.", expect_answer=True)
            return state
        return await _confirm_files_then_node(ws, chosen, await live_node_names())

    if stage == "await_node":
        options = state.get("options") or await live_node_names()
        match = match_device_answer(text, options)
        live = await live_node_names()
        if match and match not in live:
            await say(ws, f"{device_label(match)} just went offline — options now: "
                          + ", ".join(f"{i+1}. {device_label(n)}" for i, n in enumerate(live)))
            return {**state, "options": live}
        if not match:
            opts = ", ".join(f"{i+1}. {device_label(n)}" for i, n in enumerate(options))
            await say(ws, f"Didn't catch that — say the number or the name: {opts}. "
                          f"(or say 'cancel')")
            return state
        return await confirm_and_maybe_ask_dest(ws, state, match)

    if stage == "await_dest":
        dest = parse_dest_choice(text)
        await execute_share(ws, state["files"], state["display_name"], state["node"], dest)
        return None

    return None


def device_roster():
    """Nickname facts for the system prompt, so 'what's the Vivobook really
    called?' gets a true answer instead of an invented one."""
    pairs = [f"{v['nickname']} is the nickname of the device '{k}'"
             for k, v in known_nodes().items() if v.get("nickname")]
    if not pairs:
        return ""
    return ("Device nicknames: " + "; ".join(pairs) + ". Call each device by its "
            "nickname; give the original device name only when asked for it or "
            "when reporting that device's specs. ")


def build_messages(user_text, image_b64=None, task_ctx=None, resume_ctx=None, speaker=None):
    if speaker:
        who = (f"The person speaking right now is {speaker} — you recognized their voice. "
               f"You may address them as {speaker}, naturally and sparingly. ")
    else:
        who = ("The person you're talking to is the user; you do NOT know their name. "
               "Never address them by any name unless they tell you theirs. ")
    system_prompt = (
        f"{memory.get_time_context()}"
        "YOUR name is Lucy. You are the AI assistant — Lucy is YOU. "
        + who +
        "Never call the user 'Lucy'. If the user says 'Lucy', they are talking TO you, "
        "not stating their own name. "
        "You have a calm, natural female voice. Always reply in English only. "
        "Talk like a real person — casual, direct, warm, no filler phrases. "
        "Keep replies short unless the user asks for detail. "
        "Never use numbered lists, bullet points, asterisks, emojis, or markdown formatting. "
        "When listing options, use natural connectors like 'first', 'then', 'or', 'and' instead of numbers. "
    )
    if task_ctx:
        # No past context here on purpose: stale readings from earlier turns
        # bleed into the numbers otherwise. Fresh data only.
        system_prompt += (
            " " + task_ctx +
            " Report EVERY reading above WITH its exact number — never omit a number, "
            "never replace one with words like 'a lot' or 'quite busy', never calculate, "
            "convert, average, or recall numbers from earlier conversation. Keep it to "
            "two or three natural sentences. Never invent a method, mechanism, or extra "
            "step that isn't stated above (no email, no cloud, no upload links, nothing) "
            "— what happened is exactly what is described, full stop."
        )
    else:
        system_prompt += (
            "If the user asks about current device stats, temperatures, memory, "
            "battery or disk and this prompt contains no verified live readings, "
            "say you couldn't get fresh data right now — NEVER quote numbers from "
            "earlier conversation as if they were current. "
            f"{device_roster()}"
            f"Past context: {memory.get_past_context()}."
        )
        # Resume offer itself is appended deterministically after the reply
        # (see summarize_topic + the ws loop); here we just make sure she
        # answers the current question normally and doesn't pre-empt it.
    if image_b64:
        content = (user_text or "Briefly describe what you see.") + " Be concise."
        msg = {"role": "user", "content": content, "images": [image_b64]}
    else:
        msg = {"role": "user", "content": user_text}
    return [{"role": "system", "content": system_prompt}, msg]


def format_task_data(capability, action, node, data):
    """Pre-digest device data into exact prose so the LLM never reads raw
    JSON or does arithmetic — small models garble both."""
    if capability == "files.transfer" and isinstance(data, dict):
        mb = data.get("bytes", 0) / 1e6
        if "saved" in data:
            return (f"Verified: the file '{data['saved']}' ({mb:.1f} MB) was just delivered "
                    f"to the shared folder on device '{device_label(node)}'.")
        if "sent" in data:
            return (f"Verified: the file '{data['sent']}' ({mb:.1f} MB) was just fetched "
                    f"from device '{device_label(node)}' into the core shared folder.")
    if capability == "files.transfer" and isinstance(data, list):
        names = ", ".join(f.get("name", "?") for f in data) or "none"
        return f"Verified: files in the shared folder on device '{device_label(node)}': {names}."
    if capability == "monitoring.system" and isinstance(data, dict):
        # specs speak the nickname but carry the original name, per spec
        label = device_label(node)
        who = label if label == node else f"{label} (original device name: {node})"
        p = []
        if "cpu_percent" in data:
            p.append(f"cpu at {data['cpu_percent']:.0f} percent")
        if "ram_used_gb" in data and "ram_total_gb" in data:
            p.append(f"ram {data['ram_used_gb']:.1f} of {data['ram_total_gb']:.1f} gigabytes used"
                     + (f" ({data['ram_percent']:.0f} percent)" if "ram_percent" in data else ""))
        if "disk_percent" in data:
            p.append(f"disk usage at {data['disk_percent']:.0f} percent")
        if "battery_percent" in data:
            p.append(f"battery {data['battery_percent']:.0f} percent"
                     + (" plugged in" if data.get("on_ac") else " on battery power"))
        gpu = data.get("gpu")
        if isinstance(gpu, dict):
            p.append(f"gpu {gpu.get('name', '')} at {gpu.get('util', '?')} load, "
                     f"{gpu.get('vram_used', '?')} vram, {gpu.get('temp_c', '?')} degrees")
        if "uptime_h" in data:
            p.append(f"up for {data['uptime_h']:.1f} hours")
        return f"Verified live readings from device '{who}': " + "; ".join(p) + "."
    # generic fallback — flat key/value prose beats raw JSON
    if isinstance(data, dict):
        flat = "; ".join(f"{k} is {v}" for k, v in data.items())
        return f"Verified live result from '{node}' ({capability}.{action}): {flat}."
    return f"Verified live result from '{node}' ({capability}.{action}): {data}."


async def summarize_topic(text):
    """One focused call to name what an interrupted sentence was about.
    A small model handles a single narrow task far better than a two-part
    'answer then also offer' instruction, which it reliably ignores."""
    try:
        raw = ""
        async for tok in registry.stream(
                "llm.local", "chat",
                messages=[{"role": "user", "content":
                    "In 3 to 6 words, name the topic of this unfinished sentence. "
                    "Reply with ONLY the topic phrase, no quotes, no punctuation:\n"
                    f"\"{text[:300]}\""}],
                options={"temperature": 0, "num_predict": 20}):
            raw += tok
        topic = raw.strip().strip('".').split("\n")[0].strip()
        return topic[:70] if topic else "what we were discussing"
    except Exception:
        return "what we were discussing"


async def run_planned_task(ws, text):
    """Phase 2: let the planner route the request to a device task.
    Returns (task_ctx_for_llm, already_replied). When already_replied is
    True the caller must skip the LLM turn entirely — a reply was already
    sent (used for file deliveries, where a hand-written confirmation beats
    letting the model narrate and risk inventing details)."""
    try:
        p = await planner.plan(text)
    except Exception:
        return None, False
    if not p:
        return None, False

    res = await scheduler.execute_task(registry, p["capability"], p["action"],
                                       p.get("params"), p["node"])

    if p["capability"] == "files.transfer" and p["action"] == "deliver" and res["ok"]:
        data = res["data"]
        await ws.send_json({"type": "task_note", "task_id": res.get("task_id"),
                            "text": f"{p['capability']}.{p['action']} → {res['node']} · {res['ms']}ms"})
        await say(ws, deliver_confirmation(data.get("saved", p["params"].get("name", "file")),
                                           res["node"], data.get("folder", "the shared folder"),
                                           data.get("bytes", 0)))
        return None, True

    if res["ok"]:
        await ws.send_json({"type": "task_note", "task_id": res.get("task_id"),
                            "text": f"{p['capability']}.{p['action']} → {res['node']} · {res['ms']}ms"})
        return format_task_data(p["capability"], p["action"], res["node"], res["data"]), False

    await ws.send_json({"type": "task_note", "task_id": res.get("task_id"),
                        "text": f"{p['capability']}.{p['action']} failed · {res['error']}"})
    reason = re.sub(r"[A-Za-z]+Error\(['\"]?|['\"]?\)+|RuntimeError", "", res["error"]).strip(" '\"")
    return (f"The task {p['capability']}.{p['action']} just failed. Reason: {reason}. "
            "Tell the user in ONE short apologetic sentence that it failed and why. "
            "Do not give instructions, steps, lists, or workarounds."), False


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.append(ws)
    history = memory.load_today_memory()
    pending_share = None    # in-progress guided file-share wizard, if any
    pending_connect = None  # awaiting a which-device answer for a wake
    resume_offer = None     # interrupted-reply content, kept so "continue" works
    voice_offer_made = False  # one enrollment hint per session for unknown voices
    current_turn = None     # the in-flight reply task — cancellable

    async def run_turn(msg):
        """One full conversation turn. Runs as a task so a new message or an
        interrupt can cancel it mid-reply — killing the LLM stream AND the
        TTS pipeline instead of letting her narrate to the end."""
        nonlocal pending_share, pending_connect, history, resume_offer, voice_offer_made
        audio_tasks = []
        speaker = None          # who's talking, if their voice is recognized
        offer_voice_enroll = False
        try:
            msg_type = msg.get("type")

            if msg_type == "text":
                text = msg.get("text", "").strip()
                if not text:
                    return
                if text.startswith("/"):
                    await handle_command(ws, text)
                    return
                image_b64 = msg.get("image")
                print(f"Text msg received. Image attached: {bool(image_b64)}, size: {len(image_b64) if image_b64 else 0} chars")
                await ws.send_json({"type": "status", "state": "thinking"})

            elif msg_type == "audio":
                await ws.send_json({"type": "status", "state": "thinking"})
                wav_bytes = base64.b64decode(msg["data"])

                # transcription + voice identification run in parallel
                have_speaker_id = registry.has("speech.speaker")
                name_hints = ""
                if have_speaker_id:
                    try:
                        known = await registry.call("speech.speaker", "list")
                        name_hints = ", ".join(p["name"] for p in known)
                    except Exception:
                        pass
                jobs = [registry.call("speech.stt", "transcribe",
                                      wav_bytes=wav_bytes, hints=name_hints)]
                if have_speaker_id:
                    jobs.append(registry.call("speech.speaker", "identify", wav_bytes=wav_bytes))
                results = await asyncio.gather(*jobs, return_exceptions=True)

                text = results[0] if not isinstance(results[0], Exception) else ""
                if have_speaker_id and not isinstance(results[-1], Exception):
                    speaker = results[-1].get("speaker")
                if not text:
                    await ws.send_json({"type": "status", "state": "idle"})
                    return
                image_b64 = msg.get("image")
                await ws.send_json({"type": "transcript", "text": text, "speaker": speaker})

                # "remember my voice, I'm <name>" — enroll and confirm, no LLM
                m = ENROLL_VOICE_RE.search(text)
                if m and have_speaker_id:
                    name = m.group("name").strip().strip(".").title()
                    res = await registry.call("speech.speaker", "enroll",
                                              wav_bytes=wav_bytes, name=name)
                    if res.get("ok"):
                        await say(ws, f"Got it, {name} — I know your voice now. "
                                      f"Next time you speak, I'll recognize you.")
                    else:
                        await say(ws, "I couldn't quite capture your voice — "
                                      "say a longer sentence and try again.")
                    return

                # "remember my voice" with no name — ask instead of letting
                # the LLM claim enrollment happened when nothing did
                if (have_speaker_id and not m
                        and re.search(r"remember\s+my\s+voice", text, re.I)):
                    await say(ws, "Sure — say it in one sentence: \"Lucy, remember "
                                  "my voice, I'm\" and then your name, so I can "
                                  "capture your voice and name together.")
                    return

                # unknown voice → offer enrollment once per session
                if have_speaker_id and not speaker and not voice_offer_made:
                    offer_voice_enroll = True

            else:
                return

            # Follow-up answer to "which device do you mean?"
            if pending_connect is not None:
                result = await advance_connect_choice(ws, pending_connect, text)
                if result == "PASS":
                    pending_connect = None   # not an answer — handle normally below
                else:
                    pending_connect = result
                    return

            # Guided file-share wizard — applies to typed or spoken requests
            if pending_share is not None:
                result = await advance_share_wizard(ws, pending_share, text)
                if result == "PASS":
                    pending_share = None   # not a wizard answer — handle normally below
                else:
                    pending_share = result
                    return

            # Accepted resume offer → continue the interrupted reply for real.
            # The offer is one-shot: any other response clears it.
            llm_text = text
            continuing = False
            if resume_offer is not None and not image_b64:
                if re.match(r"^(yes|yeah|yep|sure|ok(ay)?\b|continue|go on|carry on|finish|pick up|please do)",
                            text.strip().lower()):
                    llm_text = (
                        "Earlier you were interrupted mid-reply. This is what you had said so far: "
                        f"\"{resume_offer['content'][:1500]}\" — now continue naturally from exactly "
                        "where that left off. Do not repeat what was already said, do not summarize "
                        "it, do not start over. Just complete the rest of the thought."
                    )
                    continuing = True
                resume_offer = None

            context_file = msg.get("contextFile")
            wants_this_file = context_file and re.search(r"\b(this|it|that)\b", text, re.I)
            if not image_b64 and not continuing:
                # Someone is waiting on this person's yes/no — that answer
                # outranks everything else they might have meant.
                if looks_like_consent_reply(text, speaker):
                    await handle_consent_reply(ws, text, speaker)
                    return
                if wants_this_file:
                    # a file was just dropped/pasted and the user says "send this"
                    pending_share = await start_share_wizard(ws, context_file, text, speaker)
                    return
                if looks_like_rename_intent(text):
                    # never let the LLM claim "I've renamed it" — do it for real
                    await handle_rename_request(ws, text)
                    return
                if looks_like_connect_intent(text):
                    # never let the LLM improvise "Connecting now…" — this
                    # either wakes the device for real or explains honestly
                    pending_connect = await handle_connect_request(ws, text)
                    return
                if looks_like_share_intent(text) and not names_a_file(text):
                    # share intent but no explicit filename — ask what to send
                    # rather than grabbing whatever's sitting in the shared folder
                    pending_share = await start_share_wizard(ws, None, text, speaker)
                    return

            # Phase 2: planner may route this to a device task first
            task_ctx = None
            if not image_b64 and not continuing:
                task_ctx, already_replied = await run_planned_task(ws, text)
                if already_replied:
                    return

            full_reply = ""
            sentence_buf = ""
            tts_buf = ""
            first_token = True
            MIN_TTS = 20   # minimum chars before flushing any chunk

            async def send_audio_when_ready(tts_task):
                data = await tts_task
                await ws.send_json({"type": "audio_chunk", "data": data})

            def flush_tts(chunk_text):
                cleaned = clean_for_tts(chunk_text)
                if not cleaned:
                    return
                t = asyncio.create_task(registry.call("speech.tts", "synthesize", text=cleaned))
                audio_tasks.append(asyncio.create_task(send_audio_when_ready(t)))

            resume_ctx = msg.get("resumeContext")
            async for token in registry.stream(
                    "llm.local", "chat",
                    messages=build_messages(llm_text, image_b64, task_ctx, resume_ctx, speaker)):
                if first_token:
                    await ws.send_json({"type": "reply_start"})
                    first_token = False

                full_reply   += token
                sentence_buf += token
                await ws.send_json({"type": "token", "text": token})

                # flush on sentence end or newline — never on comma alone
                is_end = bool(re.search(r'(?<!\d)[.!?…]["»]?\s*$', sentence_buf))
                is_newline = '\n' in sentence_buf

                if is_end or is_newline:
                    chunk = sentence_buf.strip()
                    sentence_buf = ""
                    if chunk:
                        tts_buf += (" " if tts_buf else "") + chunk
                        if len(tts_buf) >= MIN_TTS:
                            flush_tts(tts_buf)
                            tts_buf = ""

            # Interrupt-resume: append a detailed offer to continue the earlier
            # thought, deterministically (the model won't do it reliably itself).
            # The content is kept server-side so an accepting "continue" works.
            if resume_ctx and not task_ctx and not continuing and not first_token:
                topic = await summarize_topic(resume_ctx)
                resume_line = (f" Oh, and earlier I was in the middle of telling you about "
                               f"{topic} — want me to pick up right where we left off?")
                await ws.send_json({"type": "token", "text": resume_line})
                full_reply += resume_line
                sentence_buf += resume_line
                resume_offer = {"content": resume_ctx, "topic": topic}

            # Unknown voice → one-time enrollment hint, appended deterministically
            if offer_voice_enroll and not first_token:
                voice_offer_made = True
                voice_line = (" By the way, I don't recognize your voice yet — say "
                              "\"Lucy, remember my voice, I'm\" and your name, and I'll "
                              "know it's you from then on.")
                await ws.send_json({"type": "token", "text": voice_line})
                full_reply += voice_line
                sentence_buf += voice_line

            # flush anything remaining
            tail = (tts_buf + " " + sentence_buf).strip() if sentence_buf.strip() else tts_buf.strip()
            if tail:
                flush_tts(tail)

            await ws.send_json({"type": "reply_end"})

            if audio_tasks:
                await asyncio.gather(*audio_tasks)

            # Device-status/transfer replies are ephemeral readings — never
            # persist them, or stale numbers get parroted back later.
            if not task_ctx:
                entry = {"user": text, "ai": full_reply}
                if speaker:
                    entry["speaker"] = speaker
                history.append(entry)
                if len(history) > config.MAX_DAILY_TURNS * 2:
                    history = history[-config.MAX_DAILY_TURNS:]
                memory.save_today_memory(history)
            gc.collect()

        except asyncio.CancelledError:
            # interrupted: kill in-flight TTS sends so no more audio reaches
            # the browser, close the half-open bubble, and stand down
            for t in audio_tasks:
                t.cancel()
            try:
                await ws.send_json({"type": "reply_end"})
                await ws.send_json({"type": "status", "state": "idle"})
            except Exception:
                pass
            raise
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                await ws.send_json({"type": "error", "message": repr(e)})
            except Exception:
                pass

    async def cancel_turn():
        nonlocal current_turn
        if current_turn and not current_turn.done():
            current_turn.cancel()
            try:
                await current_turn
            except (asyncio.CancelledError, Exception):
                pass
        current_turn = None

    try:
        while True:
            msg = await ws.receive_json()

            if msg.get("type") == "interrupt":
                # wake word fired or user started talking — stop mid-sentence NOW
                await cancel_turn()
                continue

            # any new user message supersedes the reply in progress
            await cancel_turn()
            current_turn = asyncio.create_task(run_turn(msg))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            await ws.send_json({"type": "error", "message": repr(e)})
        except Exception:
            pass
    finally:
        await cancel_turn()
        if ws in connected_clients:
            connected_clients.remove(ws)


def main():
    uvicorn.run(app, host=config.HOST, port=config.PORT)
