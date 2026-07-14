"""Planner — Phase 2.

Decides whether a user message needs a live device task, without the user
naming capabilities or nodes. Two safeguards keep a 3B model honest:
a keyword prefilter (casual chat never pays planner latency) and a strict
whitelist (only AUTOPLAN_CAPS, only actions a live provider actually lists).
"""
import json, re
from . import config
from .registry import registry

DEST_ALIASES = {"shared", "desktop", "downloads", "documents", "pictures"}

# Broad on purpose — a false positive costs one cheap LLM call,
# a false negative makes Lucy look deaf to the request.
KEYWORDS = [
    "stat", "monitor", "cpu", "gpu", "ram", "memory", "disk", "storage",
    "battery", "temp", "hot", "heat", "load", "usage", "uptime", "vram",
    "node", "device", "machine", "laptop", "desktop", "vivobook",
    "health", "check", "running", "online", "offline", "specs",
    "file", "send", "transfer", "share", "copy", "grab", "fetch", "move",
]


def _live_options():
    lines = []
    for cap in config.AUTOPLAN_CAPS:
        for p in registry.providers.get(cap, []):
            if p.alive:
                actions = ", ".join(p.manifest.get("actions", []))
                lines.append(f'- capability "{cap}", actions: [{actions}], node: "{p.node}"')
    return lines


def _worth_planning(text):
    t = text.lower()
    if any(k in t for k in KEYWORDS):
        return True
    node_names = {p.node.lower() for ps in registry.providers.values() for p in ps}
    return any(n in t for n in node_names)


def _looks_like_filename(name):
    """Reject anything that's obviously prose the model hallucinated
    instead of a real filename (a 3B model WILL do this)."""
    if not name or len(name) > 150 or name.count(" ") > 3:
        return False
    return bool(re.search(r"\.[A-Za-z0-9]{1,8}$", name))


async def plan(text):
    """Return {"capability", "action", "node"} or None."""
    options = _live_options()
    if not options or not _worth_planning(text) or not registry.has("llm.local"):
        return None

    # File-transfer context: the router needs the real shared-file names so
    # "send the architecture doc" maps to an exact filename.
    shared_files = []
    if "files.transfer" in config.AUTOPLAN_CAPS:
        try:
            config.SHARED_DIR.mkdir(parents=True, exist_ok=True)
            shared_files = [f.name for f in sorted(config.SHARED_DIR.iterdir()) if f.is_file()][:20]
        except Exception:
            pass
    files_line = ("Files currently in the core shared folder: "
                  + (", ".join(shared_files) if shared_files else "(empty)") + "\n") \
        if any("files.transfer" in o for o in options) else ""

    prompt = (
        "You route user requests to device tasks. Live tasks available:\n"
        + "\n".join(options) + "\n" + files_line +
        "\nThe message already passed a device-topic filter, so lean toward routing. "
        "ANY question about a machine's current state — cpu, ram, memory, disk, battery, "
        "temperature, load, uptime, health, or casual forms like \"how is the laptop doing\" "
        "— requires the monitoring task. "
        "Sending/copying a shared file TO a device is action \"deliver\" on that device; "
        "getting a file FROM a device into the core is action \"fetch\" on that device. "
        "When picking a file, use the exact filename from the shared folder list — "
        "never invent one. Only answer null when no task fits.\n\n"
        "Examples (format reference ONLY — never copy their text into your answer):\n"
        'message: "how\'s the vivobook doing?" -> {"capability": "monitoring.system", "action": "read_stats", "node": null}\n'
        'message: "check the gpu temps" -> {"capability": "monitoring.system", "action": "read_stats", "node": null}\n'
        'message: "send widget.zip to the vivobook" -> {"capability": "files.transfer", "action": "deliver", "node": "vivobook", "params": {"name": "widget.zip"}}\n'
        'message: "send widget.zip to the vivobook desktop" -> {"capability": "files.transfer", "action": "deliver", "node": "vivobook", "params": {"name": "widget.zip", "dest": "desktop"}}\n'
        'message: "grab notes.txt from NODE-1" -> {"capability": "files.transfer", "action": "fetch", "node": "NODE-1", "params": {"name": "notes.txt"}}\n'
        'message: "what files are on the vivobook?" -> {"capability": "files.transfer", "action": "list", "node": "vivobook", "params": {}}\n'
        'message: "this thing was expensive" -> null\n\n'
        f'Now route this exact message, considering ONLY its literal content: "{text}"\n'
        "Reply with ONLY one line of JSON in the same format, nothing else."
    )
    try:
        raw = ""
        async for tok in registry.stream(
                "llm.local", "chat",
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 80}):
            raw += tok
    except Exception as e:
        print(f"Planner: llm error {e!r}")
        return None

    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        print(f"Planner: no task ({raw.strip()[:120]!r})")
        return None
    try:
        p = json.loads(m.group(0))
    except Exception:
        print(f"Planner: bad json ({raw.strip()[:120]!r})")
        return None

    cap, action = p.get("capability"), p.get("action")
    if cap not in config.AUTOPLAN_CAPS:
        print(f"Planner: capability {cap!r} not in whitelist")
        return None
    live = [x for x in registry.providers.get(cap, []) if x.alive]
    if not live or not any(action in (x.manifest.get("actions") or []) for x in live):
        print(f"Planner: no live provider offers {cap}.{action}")
        return None
    node = p.get("node")
    if node and not any(x.node.lower() == str(node).lower() for x in live):
        node = None  # unknown node name — let the scheduler pick

    params = p.get("params") or {}
    if cap == "files.transfer" and action in ("deliver", "fetch"):
        from .nodes import safe_name
        raw_name = params.get("name")
        name = safe_name(raw_name)
        if not name or not _looks_like_filename(name):
            print(f"Planner: rejected hallucinated filename {raw_name!r} for {text!r}")
            return None
        if action == "deliver" and not any(name.lower() == f.lower() for f in shared_files):
            print(f"Planner: {name!r} not in core shared folder — refusing to dispatch")
            return None
        # The file must actually be referenced in the request — otherwise random
        # speech ("do you know this brand?") could trigger a transfer of whatever
        # happens to be sitting in the shared folder.
        if action in ("deliver", "fetch"):
            stem = re.sub(r"\.[A-Za-z0-9]+$", "", name).lower()
            words = [w for w in re.split(r"[\W_]+", stem) if len(w) > 2]
            tl = text.lower()
            if name.lower() not in tl and not any(w in tl for w in words):
                print(f"Planner: {name!r} not referenced in request {text!r} — refusing")
                return None
        params = {"name": name}
        if action == "deliver":
            dest = str(p.get("params", {}).get("dest", "shared")).strip().lower()
            params["dest"] = dest if dest in DEST_ALIASES else "shared"

    print(f"Planner: routed -> {cap}.{action} node={node} params={params}")
    return {"capability": cap, "action": action, "node": node, "params": params}
