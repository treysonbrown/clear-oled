#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import contextlib

from display_client import DisplayConnectionError, DisplayUpdateClient
from oled_display import fit_transcript_tail_text
from transcription_audio import (
    FRAME_BYTES,
    SAMPLE_RATE,
    AudioDependencyError,
    AudioPhraseDetector,
    MicrophoneInput,
    MicrophonePermissionError,
    list_audio_devices,
)
from transcription_engine import TranscriptionEngineError, WhisperTranscriptionEngine
from transcription_events import EventBroadcaster
from transcription_store import TranscriptionStore
from transcription_types import (
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    DISPLAY_STATE_CONNECTED,
    DISPLAY_STATE_DISCONNECTED,
    DISPLAY_STATE_UNKNOWN,
    MIC_STATE_ERROR,
    MIC_STATE_IDLE,
    MIC_STATE_LISTENING,
    MIC_STATE_PERMISSION_DENIED,
    MIC_STATE_READY,
    SERVICE_STATE_IDLE,
    SERVICE_STATE_RUNNING,
    SERVICE_STATE_STARTING,
    SERVICE_STATE_STOPPING,
    TranscriptionConflictError,
    TranscriptionNotFoundError,
    format_utc,
    normalize_transcript_text,
    now_utc,
)


class TranscriptionService:
    def __init__(
        self,
        *,
        store,
        display_url,
        token,
        broadcaster=None,
        audio_input_factory=None,
        detector_factory=None,
        engine_factory=None,
        display_client_factory=None,
        debug=False,
    ):
        self.store = store
        self.display_url = display_url
        self.token = token
        self.broadcaster = broadcaster or EventBroadcaster()
        self.audio_input_factory = audio_input_factory or (lambda device_id=None: MicrophoneInput(device_id=device_id))
        self.detector_factory = detector_factory or (lambda: AudioPhraseDetector())
        self.engine_factory = engine_factory or (
            lambda model_name=None: WhisperTranscriptionEngine(
                model_name=model_name or DEFAULT_MODEL,
                language=DEFAULT_LANGUAGE,
            )
        )
        self.display_client_factory = display_client_factory or (
            lambda: DisplayUpdateClient(
                url=self.display_url,
                token=self.token,
                device="clear-oled-transcription-web",
            )
        )
        self.debug = debug
        self.control_lock = asyncio.Lock()
        self.service_state = SERVICE_STATE_IDLE
        self.mic_state = MIC_STATE_IDLE
        self.display_state = DISPLAY_STATE_UNKNOWN
        self.display_connected = None
        self.current_partial = ""
        self.current_oled_text = ""
        self.last_error = None
        self.last_display_error = None
        self.active_session = None
        self.active_engine = None
        self.session_task = None
        self.stop_event = None
        self.audio_input = None
        self.audio_queue = None
        self.display_client = None

    async def startup(self):
        self.store.mark_interrupted_sessions()

    async def shutdown(self):
        await self.stop_session()

    def list_audio_devices(self):
        return list_audio_devices()

    def get_session_detail(self, session_id):
        detail = self.store.get_session_with_segments(session_id)
        return {
            "session": detail["session"].to_api_dict(),
            "segments": [segment.to_api_dict() for segment in detail["segments"]],
        }

    def list_sessions(self, *, limit=20):
        return [session.to_api_dict() for session in self.store.list_sessions(limit=limit)]

    def status_snapshot(self):
        return {
            "service_state": self.service_state,
            "mic_state": self.mic_state,
            "display_state": self.display_state,
            "display_connected": self.display_connected,
            "last_error": self.last_error,
            "last_display_error": self.last_display_error,
            "current_partial": self.current_partial,
            "current_oled_text": self.current_oled_text,
            "current_session": self.active_session.to_api_dict() if self.active_session else None,
            "engine_backend": self.active_engine.backend_name if self.active_engine else None,
            "engine_model": self.active_engine.model_name if self.active_engine else None,
        }

    async def publish_status(self):
        await self.broadcaster.publish("status", self.status_snapshot())

    async def start_session(self, *, device_id=None, model=None):
        async with self.control_lock:
            if self.session_task and not self.session_task.done():
                raise TranscriptionConflictError("A transcription session is already running.")

            self.service_state = SERVICE_STATE_STARTING
            self.mic_state = MIC_STATE_READY
            self.last_error = None
            self.last_display_error = None
            self.current_partial = ""
            await self.publish_status()

            try:
                self.active_engine = await asyncio.to_thread(self.engine_factory, model or DEFAULT_MODEL)
                self.audio_input = self.audio_input_factory(device_id=device_id)
                self.audio_queue = asyncio.Queue(maxsize=256)
                self.audio_input.start(loop=asyncio.get_running_loop(), queue=self.audio_queue)
            except MicrophonePermissionError as exc:
                self.service_state = SERVICE_STATE_IDLE
                self.mic_state = MIC_STATE_PERMISSION_DENIED
                self.last_error = str(exc)
                await self.publish_status()
                raise
            except (AudioDependencyError, TranscriptionEngineError, OSError, RuntimeError) as exc:
                self.service_state = SERVICE_STATE_IDLE
                self.mic_state = MIC_STATE_ERROR
                self.last_error = str(exc)
                await self.publish_status()
                raise
            except Exception:
                self.service_state = SERVICE_STATE_IDLE
                self.mic_state = MIC_STATE_ERROR
                self.last_error = "Unable to start the transcription session."
                await self.publish_status()
                raise

            device_name = self._resolve_device_name(device_id)
            self.display_client = self.display_client_factory()
            self.active_session = self.store.create_session(
                device_name=device_name,
                model_name=self.active_engine.model_name,
                language=DEFAULT_LANGUAGE,
                display_connected=False,
            )
            self.stop_event = asyncio.Event()
            self.service_state = SERVICE_STATE_RUNNING
            self.mic_state = MIC_STATE_LISTENING
            await self._try_connect_display()
            self.session_task = asyncio.create_task(self._run_session(self.active_session.id))
            await self.broadcaster.publish("session_started", self.active_session.to_api_dict())
            await self.publish_status()
            return self.active_session.to_api_dict()

    async def stop_session(self):
        async with self.control_lock:
            if self.session_task is None:
                return None

            task = self.session_task
            self.service_state = SERVICE_STATE_STOPPING
            if self.stop_event is not None:
                self.stop_event.set()
            if self.audio_input is not None:
                self.audio_input.stop()
            if self.audio_queue is not None:
                with contextlib.suppress(asyncio.QueueFull):
                    self.audio_queue.put_nowait(None)
            await self.publish_status()

        await task
        return True

    def _resolve_device_name(self, device_id):
        try:
            devices = self.list_audio_devices()
        except Exception:
            return None
        for device in devices:
            if device["id"] == str(device_id):
                return device["name"]
        for device in devices:
            if device["is_default"]:
                return device["name"]
        return None

    async def _try_connect_display(self):
        if not self.display_client or not hasattr(self.display_client, "_ensure_websocket"):
            return
        try:
            await self.display_client._ensure_websocket()
            self.display_state = DISPLAY_STATE_CONNECTED
            self.display_connected = True
            self.last_display_error = None
            self.active_session = self.store.update_session(
                self.active_session.id,
                display_connected=True,
                last_error=self.active_session.last_error,
            )
        except Exception as exc:
            self.display_state = DISPLAY_STATE_DISCONNECTED
            self.display_connected = False
            self.last_display_error = str(exc)
            self.active_session = self.store.update_session(
                self.active_session.id,
                display_connected=False,
                last_error=self.last_display_error,
            )

    async def _send_display_text(self, text):
        normalized = normalize_transcript_text(text)
        if not normalized or self.display_client is None:
            return
        try:
            await self.display_client.send_text(normalized)
            self.display_state = DISPLAY_STATE_CONNECTED
            self.display_connected = True
            self.last_display_error = None
            self.current_oled_text = normalized
            self.active_session = self.store.update_session(
                self.active_session.id,
                display_connected=True,
                last_error=self.active_session.last_error,
            )
        except (DisplayConnectionError, Exception) as exc:
            self.display_state = DISPLAY_STATE_DISCONNECTED
            self.display_connected = False
            self.last_display_error = str(exc)
            self.active_session = self.store.update_session(
                self.active_session.id,
                display_connected=False,
                last_error=self.last_display_error,
            )
            await self.publish_status()

    async def _handle_partial(self, session_id, event):
        if self.active_session is None or session_id != self.active_session.id:
            return
        text = await asyncio.to_thread(
            self.active_engine.transcribe_pcm16,
            event.audio_bytes,
            sample_rate=SAMPLE_RATE,
        )
        if not text or text == self.current_partial:
            return
        oled_text = fit_transcript_tail_text(text)
        self.current_partial = text
        await self._send_display_text(oled_text)
        await self.broadcaster.publish(
            "partial",
            {
                "session_id": session_id,
                "started_at_utc": format_utc(event.started_at),
                "ended_at_utc": format_utc(event.ended_at),
                "text": text,
                "oled_text": oled_text,
            },
        )
        await self.publish_status()

    async def _handle_final(self, session_id, event):
        if self.active_session is None or session_id != self.active_session.id:
            return
        text = await asyncio.to_thread(
            self.active_engine.transcribe_pcm16,
            event.audio_bytes,
            sample_rate=SAMPLE_RATE,
        )
        self.current_partial = ""
        if not text:
            await self.publish_status()
            return

        oled_text = fit_transcript_tail_text(text)
        segment = self.store.create_segment(
            session_id=session_id,
            started_at=event.started_at,
            ended_at=event.ended_at,
            text=text,
            oled_text=oled_text,
            created_at=now_utc(),
        )
        await self._send_display_text(oled_text)
        await self.broadcaster.publish("final_segment", segment.to_api_dict())
        await self.publish_status()

    async def _run_session(self, session_id):
        detector = self.detector_factory()
        session_error = None

        try:
            while True:
                if self.stop_event.is_set() and self.audio_queue.empty():
                    break
                try:
                    payload = await asyncio.wait_for(self.audio_queue.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue

                if payload is None:
                    if self.stop_event.is_set():
                        break
                    continue

                for index in range(0, len(payload), FRAME_BYTES):
                    frame_bytes = payload[index : index + FRAME_BYTES]
                    if len(frame_bytes) < FRAME_BYTES:
                        continue
                    events = detector.feed(frame_bytes)
                    for event in events:
                        if event.type == "phrase_started":
                            self.current_partial = ""
                            await self.publish_status()
                        elif event.type == "partial_ready":
                            await self._handle_partial(session_id, event)
                        elif event.type == "phrase_finished":
                            await self._handle_final(session_id, event)

            trailing = detector.flush()
            if trailing is not None:
                await self._handle_final(session_id, trailing)
        except Exception as exc:
            session_error = str(exc)
            self.last_error = session_error
            self.mic_state = MIC_STATE_ERROR
            await self.broadcaster.publish("error", {"message": session_error, "session_id": session_id})
        finally:
            if self.audio_input is not None:
                self.audio_input.stop()
            if self.display_client is not None:
                with contextlib.suppress(Exception):
                    await self.display_client.close()

            if self.active_session and self.active_session.id == session_id:
                if session_error:
                    self.active_session = self.store.mark_failed(
                        session_id,
                        last_error=session_error,
                        display_connected=self.display_connected,
                    )
                else:
                    self.active_session = self.store.mark_stopped(
                        session_id,
                        display_connected=self.display_connected,
                        last_error=self.active_session.last_error,
                    )
                stopped_session = self.active_session
                self.service_state = SERVICE_STATE_IDLE
                self.mic_state = MIC_STATE_IDLE
                self.current_partial = ""
                self.session_task = None
                self.stop_event = None
                self.audio_input = None
                self.audio_queue = None
                self.display_client = None
                self.active_engine = None
                self.active_session = None
                await self.broadcaster.publish("session_stopped", stopped_session.to_api_dict())
                await self.publish_status()
