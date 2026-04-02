#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from transcription_events import format_sse_event
from transcription_service import TranscriptionService
from transcription_store import TranscriptionStore
from transcription_types import DEFAULT_MODEL, TranscriptionConflictError, TranscriptionNotFoundError

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except ModuleNotFoundError:
    FastAPI = None
    HTTPException = None
    FileResponse = None
    StreamingResponse = None
    StaticFiles = None
    BaseModel = object

try:
    import uvicorn
except ModuleNotFoundError:
    uvicorn = None


REPO_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = REPO_ROOT / "static"
FRONTEND_ROOT = STATIC_ROOT / "transcription"
FRONTEND_INDEX = FRONTEND_ROOT / "index.html"


class StartSessionPayload(BaseModel):
    device_id: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class TranscriptionAppConfig:
    db_path: Path
    display_url: str
    token: str
    static_root: Path = STATIC_ROOT
    frontend_root: Path = FRONTEND_ROOT


def require_web_dependencies():
    if FastAPI is None or uvicorn is None:
        raise RuntimeError(
            "The transcription web app requires `fastapi` and `uvicorn`. "
            "Install them with `python3 -m pip install fastapi uvicorn`."
        )


def create_app(config, *, service=None):
    require_web_dependencies()
    store = TranscriptionStore(config.db_path)
    service = service or TranscriptionService(
        store=store,
        display_url=config.display_url,
        token=config.token,
    )

    @asynccontextmanager
    async def lifespan(app):
        await service.startup()
        try:
            yield
        finally:
            await service.shutdown()

    app = FastAPI(title="clear-oled transcription console", lifespan=lifespan)
    app.state.store = store
    app.state.service = service
    app.state.config = config

    app.mount("/static", StaticFiles(directory=config.static_root), name="static")

    @app.get("/")
    async def root():
        frontend_index = config.frontend_root / "index.html"
        if not frontend_index.exists():
            raise HTTPException(status_code=503, detail="Frontend bundle is missing. Build the React app first.")
        return FileResponse(frontend_index)

    @app.get("/api/status")
    async def get_status():
        return service.status_snapshot()

    @app.get("/api/audio-devices")
    async def get_audio_devices():
        return service.list_audio_devices()

    @app.post("/api/session/start")
    async def start_session(payload: StartSessionPayload | None = None):
        try:
            session = await service.start_session(
                device_id=(payload.device_id if payload else None),
                model=(payload.model if payload and payload.model else DEFAULT_MODEL),
            )
        except TranscriptionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return session

    @app.post("/api/session/stop")
    async def stop_session():
        await service.stop_session()
        return {"ok": True}

    @app.get("/api/sessions")
    async def get_sessions(limit: int = 20):
        return service.list_sessions(limit=limit)

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str):
        try:
            return service.get_session_detail(session_id)
        except TranscriptionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/events")
    async def stream_events():
        queue = await service.broadcaster.subscribe()

        async def iterator():
            try:
                yield format_sse_event("status", service.status_snapshot())
                while True:
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield payload
                    except asyncio.TimeoutError:
                        yield b": keep-alive\n\n"
            finally:
                await service.broadcaster.unsubscribe(queue)

        return StreamingResponse(iterator(), media_type="text/event-stream")

    return app


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run the clear-oled transcription web app.")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=8090, help="TCP port to bind.")
    parser.add_argument(
        "--db-path",
        default=os.getenv("CLEAR_OLED_TRANSCRIPTION_DB_PATH", "./data/transcription.db"),
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
    args = parser.parse_args(argv)
    if not args.display_url:
        parser.error("--display-url or CLEAR_OLED_DISPLAY_URL is required.")
    if not args.token:
        parser.error("--token or CLEAR_OLED_DISPLAY_TOKEN is required.")
    return args


def main(argv=None):
    require_web_dependencies()
    args = parse_args(argv)
    config = TranscriptionAppConfig(
        db_path=Path(args.db_path),
        display_url=args.display_url,
        token=args.token,
    )
    app = create_app(config)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
