# Lucy Distributed Architecture — Design Doc v1

> Lucy as an operating system for your digital life: capability-based task
> routing across every device you own. This doc is the *buildable* version —
> scoped so each phase ships something Lucy can actually do, on hardware that
> exists today.

---

## 0. The core principle

**Nodes are dumb. Lucy lives in exactly one place.**

Personality, conversation, memory, planning, and scheduling live on the Core.
Every other device runs a thin agent that can only do three things: announce
what it's capable of, execute a task, and report health. The moment a node
gets "smart," debugging a multi-device assistant becomes archaeology.

---

## 1. Components

```
┌────────────────────────── LUCY CORE (main PC) ──────────────────────────┐
│                                                                          │
│  Conversation Manager ── existing FastAPI + Ollama + Whisper + TTS       │
│  Planner ───────────── LLM function-calling → [{capability, action,…}]  │
│  Capability Registry ── who can do what, right now                       │
│  Scheduler ─────────── pick a provider for each subtask                  │
│  Task Queue ────────── SQLite-backed, leases, retries, idempotency       │
│  Memory ────────────── centralized, single source of truth               │
│  Task Log ──────────── every decision recorded, visible in UI            │
│                                                                          │
│  WS endpoints:  /ws (browser UI — existing)   /node (agents)             │
└──────────────────────────────────────────────────────────────────────────┘
                 ▲                    ▲                    ▲
        WSS + token           WSS + token           WSS + token
                 │                    │                    │
        ┌────────┴───────┐  ┌────────┴───────┐  ┌────────┴────────┐
        │  Lucy Node      │  │  Lucy Node     │  │  Lucy Node      │
        │  (2nd laptop)   │  │  (Pi / NAS)    │  │  (phone bridge) │
        │  plugins/       │  │  plugins/      │  │  ntfy/Tasker    │
        └────────────────┘  └────────────────┘  └─────────────────┘
```

### Lucy Node (~300 lines, identical on every device)
1. Connect to Core, send `hello` (name, capabilities, plugin versions, protocol version)
2. Heartbeat every 5s (cpu, ram, gpu, vram, battery, power source)
3. Receive task → dispatch to plugin → stream result/error back
4. That's all. No planning, no memory, no personality.

### Plugin = folder with a manifest
```
plugins/
  gpu_monitor/
    manifest.json      ← capability, actions, requirements, permissions
    plugin.py          ← class with run(action, params) -> result
```

```json
{
  "capability": "monitoring.gpu",
  "provider": "hwinfo",
  "actions": ["read_temps", "read_vram", "watch"],
  "requires": {"os": "windows", "hardware": "nvidia"},
  "permissions": ["sensors"],
  "version": "0.1.0"
}
```

Permissions are **declared now, enforced later**. You write every plugin
yourself for the foreseeable future; sandboxing your own code is procrastination.

---

## 2. Protocol (WebSocket, JSON)

Every message: `{"v": 1, "type": ..., "id": ..., "ts": ...}` — protocol
version in every envelope, because node/core version skew *will* happen.

Core ← Node:
- `hello {node, capabilities[], plugins{name:version}, os, arch}`
- `heartbeat {cpu, ram, gpu, vram, battery, on_ac}`
- `result {task_id, ok, data | error, duration_ms}`
- `progress {task_id, pct, note}` (optional, long tasks)

Core → Node:
- `task {task_id, capability, action, params, timeout_s, idempotency_key}`
- `cancel {task_id}`
- `reload_plugins {}`

**Why WebSockets and not gRPC/MQTT/ZeroMQ:**
- Already in Lucy's stack (browser UI uses it) — one mental model
- Persistent + bidirectional + firewall/NAT friendly on Windows
- JSON is debuggable with your own eyes at 2am
- gRPC earns its keep at dozens of nodes / polyglot teams — you are one
  person with 2–5 nodes. Revisit if node count hits double digits.
- MQTT joins later as a *bridge plugin* for IoT sensors, not as the backbone.
- ZeroMQ: no auth story out of the box, wrong layer of abstraction here.

---

## 3. Scheduler

**v1 (ship this):** capability filter → prefer local → least-loaded.

```python
def pick(task, nodes):
    capable = [n for n in nodes if task.capability in n.caps and n.alive]
    if not capable: return None            # → task fails visibly in chat
    local = [n for n in capable if n.is_core_host]
    pool = local or capable
    return min(pool, key=lambda n: n.load_score)   # cpu+ram+gpu blend
```

**v2 (only after 3+ nodes and real contention):** add scoring for battery /
thermal / estimated duration / privacy tier (local-only tasks never leave LAN)
/ cost (cloud nodes). The *interface* (`pick(task, nodes) -> node`) stays
identical so v2 is a drop-in.

A cost-model scheduler with two nodes is dead code wearing a lab coat.

---

## 4. Task lifecycle

```
PENDING → LEASED(node, deadline) → RUNNING → DONE
                     │                │
                     │                ├→ FAILED(err) → retry w/ backoff (max 3)
                     │                │        └→ EXHAUSTED → surface in chat
                     └→ lease expired / node died → back to PENDING (next node)
```

- Every task carries an **idempotency_key**; side-effect plugins (notify,
  file-write, automation) must dedupe on it — retries otherwise double-fire.
- Long tasks may checkpoint via `progress`; resume is plugin-opt-in, not core magic.
- **Node death is normal, not exceptional.** Windows laptops sleep, lids
  close, Wi-Fi flaps. Heartbeat stale at 15s → node marked `away`, its leases
  recycle. Design assumes nodes constantly appear/disappear.

---

## 4b. Self-describing nodes — nobody ever types specs

Lucy is **not** optimized for any specific machine. The Core contains zero
hardware knowledge; every node probes *itself* at startup and reports what it
found. Any future desktop, Pi, or cloud box joins identically.

What `node.py` auto-detects on boot:

| Probe | How | Derived capability |
|---|---|---|
| CPU cores / arch | `psutil`, `platform` | `compute.cpu {cores, arch}` |
| RAM | `psutil` | meta on all capabilities |
| NVIDIA GPU + VRAM | `nvidia-smi` | `compute.cuda {vram_gb}` |
| AMD/Intel GPU | WMI query | `compute.gpu {kind}` |
| Battery present? | `psutil.sensors_battery()` | desktop → always task-eligible; laptop → battery-aware |
| Camera | OpenCV probe | `vision.camera` |
| Microphone | pyaudio device list | `audio.capture` |
| Ollama installed? | port 11434 check + model list | `llm.local {models[], vram_fit}` |
| OS + automation | platform | `automation.windows` etc. |

The scheduler then compares **reported numbers**, not device names: an LLM
task filters on `compute.cuda` and sorts by free VRAM; ambient monitoring
prefers the lowest-power node that has the sensor. "Use the advantageous
part for the task" falls out of the data automatically.

### Adding a brand-new device (the whole ritual)

1. You tell Lucy: *"add my desktop"*
2. She replies with a one-line bootstrap command (fresh token baked in):
   ```powershell
   irm http://<core-ip>:8000/node/bootstrap.ps1 | iex
   ```
3. On the new machine that line downloads the agent, writes its config,
   self-probes, and connects.
4. Lucy announces in chat: *"new node joined: DESKTOP-7F2K — RTX 3080 (10GB),
   32GB RAM, no battery, no camera. Grant it compute + automation?"*
5. You approve. Done — it's now schedulable.

No spec sheets, no manual registration, no editing config on the Core.

## 5. Discovery

- **mDNS** (python-zeroconf): Core advertises `_lucy._tcp.local`; nodes find it.
- Fallback: `core_url` in node config file (mDNS is flaky on some Windows
  networks / guest VLANs).
- Registry itself is just a table in the Core — mDNS only solves "where is Core."

## 6. Security (right-sized)

- Pre-shared token per node (generated by Core, pasted once into node config)
- TLS via self-signed cert pinned in node config (or plain WS on trusted LAN
  for phase 1, flag it in UI)
- Capability grants per node stored in Core DB (`vivobook: camera=yes,
  automation=ask`) — "ask" pushes an approval card into the chat UI
- No PKI ceremonies for your own two laptops. Add real cert infra when a
  node lives outside your LAN.

## 7. Memory

**Centralized on Core. Full stop.**
- SQLite (WAL mode): `memory`, `tasks`, `nodes`, `capabilities`, `task_log`
- Nodes get read-through cache with TTL for hot config only
- No sync protocols, no CRDTs, no eventual consistency. The moment memory
  is distributed, every bug becomes a distributed-systems bug.
- NAS later = the SQLite file + media store move there; architecture unchanged.

## 8. Database schema (Core)

```sql
nodes(id, name, token_hash, os, last_seen, status, is_core_host)
capabilities(node_id, capability, provider, version, meta_json)
tasks(id, parent_id, capability, action, params_json, state, node_id,
      lease_deadline, attempts, idempotency_key, created, finished,
      result_json, error)
task_log(id, task_id, ts, event, detail_json)      -- observability spine
memory(id, kind, key, value_json, updated)          -- existing daily logs fold in
```

## 9. Folder structure

```
lucy/
  core/
    main.py            ← today's main.py, gradually split
    planner.py
    registry.py
    scheduler.py
    queue.py
    db.py
  node/
    node.py            ← the whole agent
    config.json        ← core_url, token, node name
    plugins/
      <plugin>/manifest.json + plugin.py
  shared/
    protocol.py        ← message schemas, version const
  static/              ← existing UI
```

## 10. Roadmap — each phase ships value

| Phase | What | Lucy visibly gains |
|---|---|---|
| **0** ✅ | Refactor current features (camera, STT, TTS, vision, memory) into plugin-shaped modules **on one machine**. No networking. | Shipped 2026-07-07. Lucy behaves identically; capabilities now route through the registry. |
| **1** ✅ | `node.py` agent + hello/heartbeat + token auth + **task log UI panel**. Manual routing via `/task` and `/nodes`. | Shipped 2026-07-07. Vivobook onboarded with the one-line bootstrap (auto-installs Python/Ollama). |
| **2** ✅ | Planner (two-pass: keyword prefilter → JSON routing pass at temp 0, whitelisted to read-only caps) + task-context injection into the reply. | Shipped 2026-07-07. "how's the vivobook doing?" → routed, executed in ~425ms, answered conversationally. Casual chat skips the planner. |
| **3** ✅ | Retries + failover across providers, idempotency cache on the agent, mDNS advertise/discover, battery-aware scoring, reconnect takeover, agent self-update. | Shipped 2026-07-08. Task log shows the full attempt trail; nodes survive core IP changes and update themselves. |
| **3.5** ✅ | `vision.locate` plugin — NVIDIA LocateAnything-3B (4-bit NF4, lazy-loaded, idle-unloaded) + `/api/vision/detect` + live overlay in the camera droplet. | Shipped 2026-07-12. Camera went from capture-only to real-time open-vocabulary detection: type "red cup" (or nothing = everything) and boxes track it live. |
| **3.6** ✅ | Settings panel (`/api/settings`, data/settings.json) + "Connect devices through Lucy": node ip/MAC recorded on join, deterministic connect-intent handler with a which-device follow-up state, Wake-on-LAN with honest follow-up. Device nicknames ("rename X to Vivobook") replace displayed names everywhere, original kept for specs. Pairing command surfaced in the nodes panel. | Shipped 2026-07-13. "Connect to my laptop" now wakes the device for real (or explains exactly why it can't) instead of the LLM inventing "Connecting now…". Snapshot: D:\Lucy_AI V0.196. |
| **4** | Phone via ntfy (notifications) + Tasker/Shortcuts bridge; MQTT bridge plugin for IoT. | "…and notify me on my phone." |
| **5** | Task graphs (DAG deps), cloud GPU node, NAS memory move. | Multi-step autonomous pipelines. |

---

## 11. Critique of the original prompt (as requested)

1. **It's a distributed-systems project wearing an assistant costume.** The
   dream dies in month 3 when Lucy has heartbeats and registries but got no
   smarter. Antidote: every phase must ship a user-visible capability, and
   Phase 0 is deliberately *just refactoring*.

2. **The scheduler is over-specified by ~2 years.** GPU/VRAM/thermal/latency/
   cost scoring across 2 nodes is an if-statement with delusions. Keep the
   interface, ship the if-statement.

3. **Task graphs are the easy 20%.** The hard 80% is the *planner* reliably
   decomposing fuzzy requests. A 3B local model will produce garbage plans;
   plan-quality, not plumbing, is where the "OS for my life" vision lives or
   dies. Budget accordingly (bigger model for planning, or cloud-assist).

4. **Distributed memory was the biggest landmine in the prompt.** Centralize
   it. (§7)

5. **Missing entirely from the prompt: observability.** When Lucy silently
   does things on three machines, "what did she run, where, and why did it
   fail" is a *feature the user sees*, not ops tooling. The task_log +
   UI panel is non-negotiable and arrives in Phase 1, before auto-routing.

6. **Missing: idempotency.** Retry + notification plugin = triple pings at
   3am. Every side-effect action dedupes on idempotency_key.

7. **Missing: version skew.** Protocol version in every envelope from day 1.

8. **Security was simultaneously over- and under-built** — device-trust
   ceremonies for your own LAN, but no mention of the actually-scary thing:
   an `automation` capability that can drive your PC. That's why capability
   grants have an "ask" mode wired into chat approval.

9. **Hardware honesty:** the design must run on what exists *today*. Phase 1
   needs any second Windows device — the spec'd ROG/Vivobook pair is a
   future, not a prerequisite.
```
