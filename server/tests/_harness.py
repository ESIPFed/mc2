"""Shared test lifespan: the database only.

The app's own lifespan also runs the MCP session manager, whose ``run()``
may be entered once per process, so every test after the first errored in
setup. Asset/event tests need SQLite, not MCP.
"""

from contextlib import asynccontextmanager

from mapcontrol_server.config import load_config
from mapcontrol_server.database import close_db, init_db


@asynccontextmanager
async def db_lifespan(_app=None):
    await init_db(load_config())
    try:
        yield
    finally:
        await close_db()
