"""Lucy entrypoint.

The old monolith now lives as plugins under lucy/plugins/ routed by
lucy/core/. Run exactly as before:  py -3.13 main.py
"""
from lucy.core.app import main

if __name__ == "__main__":
    main()
