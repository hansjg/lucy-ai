"""Scheduler — Phase 3.

score(): local first, then cpu load with battery penalties.
pick():  capability filter -> optional node pin -> lowest score, minus
         providers already tried this task.
execute_task(): retry with backoff, failing over to a different provider
         when one exists. Every attempt and the pick reason land in task_log.
"""
import asyncio, json, time, uuid
from . import config, db


def score(p):
    """Lower is better. Local providers always win; remote nodes pay for
    load and for running on battery."""
    if p.kind == "local":
        return -1000
    stats = getattr(p, "stats", {}) or {}
    s = float(stats.get("cpu", 50.0))
    if stats.get("on_ac") is False:
        s += config.BATTERY_PENALTY
        if float(stats.get("battery", 100)) < 20:
            s += config.LOW_BATTERY_PENALTY
    return s


def pick(providers, pin=None, exclude=()):
    alive = [p for p in providers if p.alive and id(p) not in exclude]
    if pin:
        alive = [p for p in alive if p.node.lower() == pin.lower()]
    if not alive:
        return None
    return min(alive, key=score)


async def execute_task(registry, capability, action, params=None, pin=None):
    """Run one task with retries + failover, fully recorded."""
    params = params or {}
    providers = registry.providers.get(capability, [])

    task_id = uuid.uuid4().hex[:12]
    db.task_created(task_id, capability, action, json.dumps(params), pin or "?")

    tried = set()
    last_error = "no provider available"
    attempts = 1 + config.TASK_RETRIES

    for attempt in range(attempts):
        provider = pick(providers, pin, exclude=tried)
        if provider is None:
            # nothing left to try (or nothing matched the pin in the first place)
            if attempt == 0:
                last_error = f"no live provider for {capability}" + (f" on node '{pin}'" if pin else "")
            break

        db.log_event(task_id, "attempt",
                     f"#{attempt + 1} -> {provider.node} (score {score(provider):.0f})")
        t0 = time.time()
        try:
            data = await provider.call(action, _idem=task_id, **params)
            ms = int((time.time() - t0) * 1000)
            db.task_finished(task_id, ok=True,
                             result=json.dumps(data, default=str)[:4000], duration_ms=ms)
            # reflect the node that actually served it
            db.log_event(task_id, "served_by", provider.node)
            return {"ok": True, "data": data, "node": provider.node, "ms": ms, "task_id": task_id}
        except Exception as e:
            last_error = repr(e)
            tried.add(id(provider))
            db.log_event(task_id, "attempt_failed", f"{provider.node}: {last_error}")
            if attempt < attempts - 1:
                await asyncio.sleep(config.RETRY_BACKOFF_S * (attempt + 1))

    db.task_finished(task_id, ok=False, error=last_error)
    return {"ok": False, "error": last_error, "task_id": task_id}
