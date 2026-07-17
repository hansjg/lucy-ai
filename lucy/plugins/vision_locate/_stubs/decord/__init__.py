"""Import-shim for LocateAnything's remote code on Windows/Python 3.13.

decord (video decoding) has no Windows wheel for this Python, and Lucy only
ever sends still camera frames. The processor imports decord at module level
but touches it exclusively in its video branch, so an importable empty module
is enough. Passing an actual video would raise here — loudly, on purpose.
"""


def __getattr__(name):
    raise RuntimeError(
        "decord stub: video input isn't supported on this machine "
        f"(tried to use decord.{name})")
