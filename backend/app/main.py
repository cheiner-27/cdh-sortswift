"""cdh-sortswift FastAPI application."""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .db import Base, SessionLocal, engine, ensure_schema
from . import models  # noqa: F401  (register models with Base)
from .routers import (
    bulk, catalog, custom_items, expenses, exports, imports, inventory, lots,
    marketplaces, misc, orders, pricing, scans, staging,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sortswift")

POLL_TASK: asyncio.Task | None = None


async def order_poll_loop():
    """eBay order detection is poll-based (no webhooks). Configurable interval
    alongside the manual Sync button; only runs while account is connected."""
    from .services.marketplaces.sync import account, poll_orders
    while True:
        try:
            db = SessionLocal()
            try:
                acct = account(db, "ebay")
                if acct and acct.status == "connected" and not acct.credentials.get("dry_run"):
                    interval = max(1, acct.poll_interval_minutes)
                    last = acct.last_order_poll_at
                    due = last is None or (
                        datetime.now(timezone.utc) -
                        (last if last.tzinfo else last.replace(tzinfo=timezone.utc))
                        >= timedelta(minutes=interval))
                    if due:
                        result = poll_orders(db, "ebay")
                        log.info("ebay order poll: %s", result)
            finally:
                db.close()
        except Exception:
            log.exception("order poll loop error")
        await asyncio.sleep(60)


Base.metadata.create_all(engine)
ensure_schema()  # additive migrations for pre-existing on-disk DBs


@asynccontextmanager
async def lifespan(app: FastAPI):
    global POLL_TASK
    POLL_TASK = asyncio.create_task(order_poll_loop())
    yield
    POLL_TASK.cancel()


app = FastAPI(title="cdh-sortswift", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"], allow_headers=["*"],
)

for r in (misc, catalog, scans, staging, inventory, imports, exports, pricing,
          marketplaces, lots, orders, custom_items, expenses, bulk):
    app.include_router(r.router)

# Serve the built frontend if present (npm run build in frontend/)
_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
