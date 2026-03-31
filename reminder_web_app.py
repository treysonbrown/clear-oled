#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from reminder_scheduler import ReminderScheduler
from reminder_store import ReminderStore
from reminder_types import (
    ALLOWED_STATUSES,
    DEFAULT_DISPLAY_DURATION_SECONDS,
    ReminderConflictError,
    ReminderNotFoundError,
    ReminderValidationError,
    format_utc,
)

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except ModuleNotFoundError:
    FastAPI = None
    HTTPException = None
    Query = None
    FileResponse = None
    StaticFiles = None
    BaseModel = object

try:
    import uvicorn
except ModuleNotFoundError:
    uvicorn = None


REPO_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = REPO_ROOT / "static"
FRONTEND_INDEX = STATIC_ROOT / "reminders" / "index.html"


class ReminderPayload(BaseModel):
    message: str
    scheduled_at_local: str
    timezone: str


@dataclass(frozen=True)
class ReminderAppConfig:
    db_path: Path
    display_url: str
    token: str
    display_seconds: int
    static_root: Path = STATIC_ROOT


def require_web_dependencies():
    if FastAPI is None or uvicorn is None:
        raise RuntimeError(
            "The reminder web app requires `fastapi` and `uvicorn`. "
            "Install them with `python3 -m pip install fastapi uvicorn`."
        )


def map_store_error(exc):
    if isinstance(exc, ReminderValidationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, ReminderConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ReminderNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    raise exc


def create_app(config, *, start_scheduler=True):
    require_web_dependencies()
    store = ReminderStore(config.db_path)
    scheduler = ReminderScheduler(
        store=store,
        display_url=config.display_url,
        token=config.token,
        default_display_seconds=config.display_seconds,
    )

    @asynccontextmanager
    async def lifespan(app):
        if start_scheduler:
            await scheduler.start()
        try:
            yield
        finally:
            if start_scheduler:
                await scheduler.stop()

    app = FastAPI(title="clear-oled reminders", lifespan=lifespan)
    app.state.store = store
    app.state.scheduler = scheduler
    app.state.config = config

    app.mount("/static", StaticFiles(directory=config.static_root), name="static")

    @app.get("/")
    async def root():
        return FileResponse(config.static_root / "reminders" / "index.html")

    @app.get("/api/status")
    async def get_status():
        snapshot = scheduler.status_snapshot()
        return {
            "scheduler_now_utc": format_utc(snapshot["scheduler_now_utc"]),
            "browser_timezone_hint": None,
            "pi_delivery_state": snapshot["pi_delivery_state"],
            "last_delivery_error": snapshot["last_delivery_error"],
            "display_duration_seconds_default": snapshot["display_duration_seconds_default"],
        }

    @app.get("/api/reminders")
    async def get_reminders(status: str | None = None, limit: int = Query(default=50, ge=1, le=200)):
        if status is not None and status not in ALLOWED_STATUSES:
            raise HTTPException(status_code=400, detail="Unsupported reminder status.")
        reminders = store.list_reminders(status=status, limit=limit)
        return [reminder.to_api_dict() for reminder in reminders]

    @app.post("/api/reminders", status_code=201)
    async def create_reminder(payload: ReminderPayload):
        try:
            reminder = store.create_reminder(
                payload.message,
                payload.scheduled_at_local,
                payload.timezone,
                display_duration_seconds=config.display_seconds,
            )
        except Exception as exc:
            map_store_error(exc)
        return reminder.to_api_dict()

    @app.patch("/api/reminders/{reminder_id}")
    async def update_reminder(reminder_id: str, payload: ReminderPayload):
        try:
            reminder = store.update_reminder(
                reminder_id,
                message=payload.message,
                scheduled_at_local=payload.scheduled_at_local,
                timezone_name=payload.timezone,
            )
        except Exception as exc:
            map_store_error(exc)
        return reminder.to_api_dict()

    @app.delete("/api/reminders/{reminder_id}")
    async def cancel_reminder(reminder_id: str):
        try:
            reminder = store.cancel_reminder(reminder_id)
        except Exception as exc:
            map_store_error(exc)
        return reminder.to_api_dict()

    return app


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run the clear-oled reminder web app.")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=8080, help="TCP port to bind.")
    parser.add_argument(
        "--db-path",
        default=os.getenv("CLEAR_OLED_DB_PATH", "./data/reminders.db"),
        help="SQLite database path.",
    )
    parser.add_argument(
        "--display-url",
        default=os.getenv("CLEAR_OLED_DISPLAY_URL"),
        help="WebSocket URL for the Pi display server.",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("CLEAR_OLED_DISPLAY_TOKEN"),
        help="Shared auth token for the Pi display server.",
    )
    parser.add_argument(
        "--display-seconds",
        type=int,
        default=int(os.getenv("CLEAR_OLED_DISPLAY_SECONDS", DEFAULT_DISPLAY_DURATION_SECONDS)),
        help="How long reminders stay visible on the OLED.",
    )
    args = parser.parse_args(argv)
    if not args.display_url:
        parser.error("--display-url or CLEAR_OLED_DISPLAY_URL is required.")
    if not args.token:
        parser.error("--token or CLEAR_OLED_DISPLAY_TOKEN is required.")
    return args


def main(argv=None):
    require_web_dependencies()
    args = parse_args(argv)
    config = ReminderAppConfig(
        db_path=Path(args.db_path),
        display_url=args.display_url,
        token=args.token,
        display_seconds=args.display_seconds,
    )
    app = create_app(config)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
