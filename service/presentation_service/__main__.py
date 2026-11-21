"""Run the analysis service:  uv run python -m presentation_service  (from the service/ folder)."""

import argparse
import asyncio
import logging
import os
import sys
import threading

from .server import main

parser = argparse.ArgumentParser(prog="presentation_service")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8766)
parser.add_argument("--debug", action="store_true", help="log the filler checks")
parser.add_argument("--exit-with-parent", action="store_true",
                    help="exit when stdin closes (the Electron app passes this so the service never outlives it)")
args = parser.parse_args()

if args.exit_with_parent:
    def watch_parent():
        sys.stdin.read()            # returns once the app that started us closes the pipe (or dies)
        os._exit(0)
    threading.Thread(target=watch_parent, daemon=True).start()

logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logging.getLogger("websockets").setLevel(logging.WARNING)
try:
    asyncio.run(main(args.host, args.port))
except KeyboardInterrupt:
    pass
