"""Capability registry — local plugins and remote node capabilities behind
one interface. Callers say registry.call("speech.stt", "transcribe", ...) and
never learn where it ran; the scheduler picks among providers.
"""
import asyncio, importlib, json, time
from pathlib import Path
from . import config


class PluginContext:
    def __init__(self, registry, manifest):
        self.registry = registry
        self.manifest = manifest

    def emit(self, event, data=None):
        """Thread-safe event emit (plugins may run background threads)."""
        self.registry.emit(event, data)


class LocalProvider:
    kind = "local"
    node = "core"
    alive = True
    stats = {}

    def __init__(self, plugin, manifest):
        self.plugin = plugin
        self.manifest = manifest

    async def call(self, action, _idem=None, _task_id=None, **params):
        return await self.plugin.call(action, **params)

    def stream(self, action, **params):
        return self.plugin.stream(action, **params)


class RemoteProvider:
    kind = "remote"

    def __init__(self, node_conn, manifest):
        self.conn = node_conn
        self.manifest = manifest

    @property
    def node(self):
        return self.conn.name

    @property
    def alive(self):
        return (time.time() - self.conn.last_seen) < config.NODE_STALE_SECS

    @property
    def stats(self):
        return self.conn.stats

    async def call(self, action, _idem=None, _task_id=None, **params):
        return await self.conn.dispatch(self.manifest["capability"], action, params,
                                        idem=_idem, task_id=_task_id)

    def stream(self, action, **params):
        raise NotImplementedError("remote streaming lands in a later phase")


class Registry:
    def __init__(self, plugins_dir: Path):
        self.plugins_dir = plugins_dir
        self.providers = {}   # capability -> [Provider]
        self.manifests = {}   # capability -> manifest (local plugins)
        self._plugins = {}    # capability -> plugin instance (local)
        self._handlers = {}   # event -> [callbacks]
        self.loop = None

    # ── events ────────────────────────────────────────────
    def on(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event, data=None):
        handlers = self._handlers.get(event, [])
        if not handlers or self.loop is None:
            return

        def _fire():
            for h in handlers:
                res = h(data)
                if asyncio.iscoroutine(res):
                    asyncio.ensure_future(res)

        self.loop.call_soon_threadsafe(_fire)

    # ── local plugins ─────────────────────────────────────
    def discover(self):
        for mdir in sorted(self.plugins_dir.iterdir()):
            mf = mdir / "manifest.json"
            if not mf.is_file():
                continue
            manifest = json.loads(mf.read_text(encoding="utf-8"))
            module = importlib.import_module(f"lucy.plugins.{mdir.name}.plugin")
            cap = manifest["capability"]
            self._plugins[cap] = module.Plugin(PluginContext(self, manifest))
            self.manifests[cap] = manifest

    async def start_all(self):
        self.loop = asyncio.get_running_loop()
        for cap in list(self._plugins):
            try:
                await self._plugins[cap].start()
                provider = self.manifests[cap].get("provider", "")
                print(f"  [ok] {cap:<14} {provider}")
                self.providers.setdefault(cap, []).append(
                    LocalProvider(self._plugins[cap], self.manifests[cap]))
            except Exception as e:
                print(f"  [!!] {cap:<14} failed to start: {e}")
                del self._plugins[cap]

    # ── remote node capabilities ──────────────────────────
    def add_remote(self, node_conn, manifest):
        cap = manifest["capability"]
        self.providers.setdefault(cap, []).append(RemoteProvider(node_conn, manifest))

    def remove_remote(self, node_name):
        for cap in list(self.providers):
            self.providers[cap] = [
                p for p in self.providers[cap]
                if not (p.kind == "remote" and p.node == node_name)
            ]
            if not self.providers[cap]:
                del self.providers[cap]

    # ── routing (conversation pipeline path) ──────────────
    def has(self, capability):
        return any(p.alive for p in self.providers.get(capability, []))

    def _pick(self, capability, pin=None):
        from .scheduler import pick
        p = pick(self.providers.get(capability, []), pin)
        if p is None:
            raise RuntimeError(f"no live provider for capability '{capability}'")
        return p

    async def call(self, capability, action, _node=None, **params):
        return await self._pick(capability, _node).call(action, **params)

    def stream(self, capability, action, _node=None, **params):
        return self._pick(capability, _node).stream(action, **params)


# Singleton — imported by app, nodes, scheduler users
registry = Registry(config.ROOT / "lucy" / "plugins")
registry.discover()
