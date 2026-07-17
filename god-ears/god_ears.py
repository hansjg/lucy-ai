"""God Ears — standalone always-listening transcriber & Q&A assistant.

Run:  py -3.13 god_ears.py     (or run.bat)
UI:   http://localhost:8100
"""
import uvicorn

import config
import server

BANNER = r"""
   ______ ____  ____     ______ ___     ____  _____
  / ____// __ \/ __ \   / ____//   |   / __ \/ ___/
 / / __ / / / / / / /  / __/  / /| |  / /_/ /\__ \
/ /_/ // /_/ / /_/ /  / /___ / ___ | / _, _/___/ /
\____/ \____/_____/  /_____//_/  |_|/_/ |_|/____/

  always listening · daily transcripts · ask me what I heard
"""


def main():
    print(BANNER)
    print(f"  UI:        http://localhost:{config.PORT}")
    print(f"  wake:      'hey lucy' / 'god ears'")
    print(f"  storage:   {config.TRANSCRIPT_DIR}")
    print(f"  cloud:     {config.SYNC_DIR or 'off (set SYNC_DIR in config.py)'}")
    print()
    cfg = uvicorn.Config(server.app, host=config.HOST, port=config.PORT,
                         log_level="warning")
    srv = uvicorn.Server(cfg)
    server.uvicorn_server = srv
    srv.run()
    print("God Ears terminated.")


if __name__ == "__main__":
    main()
