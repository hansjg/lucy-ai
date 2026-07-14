class LucyPlugin:
    """Base class every plugin implements.

    A plugin provides exactly one capability (named in its manifest.json)
    and exposes actions via call() / stream(). Heavy initialization goes in
    start(), not __init__, so the registry controls boot order and failures.
    """

    def __init__(self, ctx):
        self.ctx = ctx  # PluginContext: .manifest, .emit(event, data)

    async def start(self):
        pass

    async def stop(self):
        pass

    async def call(self, action, **params):
        raise NotImplementedError(action)

    def stream(self, action, **params):
        """Return an async generator for streaming actions."""
        raise NotImplementedError(action)
