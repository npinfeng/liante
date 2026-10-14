"""Production WSGI entry point for Windows and Linux hosts."""

from __future__ import annotations

import logging
import os

from waitress import serve

from config import HOST, PORT
from config import PROJECT_ROOT
from deploy.runtime import acquire_server_lock


def main() -> None:
    acquire_server_lock(PROJECT_ROOT,HOST,PORT)
    os.environ['LIANTE_FACTORY_ONLY']='1'
    from backend_server import create_app
    app=create_app()
    logging.getLogger(__name__).info("starting Waitress on %s:%s", HOST, PORT)
    serve(app, host=HOST, port=PORT, threads=8, connection_limit=100,
          backlog=64, channel_timeout=30, cleanup_interval=10)


if __name__ == "__main__":
    main()
