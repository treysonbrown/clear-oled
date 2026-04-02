#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import contextlib

from display_client import DisplayConnectionError, DisplayUpdateClient
from oled_display import fit_transcript_tail_text
from transcription_audio import list_audio_devices
from transcription_engine_macos import (
    MacOSSpeechHelperError,
    MacOSSpeechPermissionError,
    MacOSSpeechSession,
)
from transcription_events import EventBroadcaster
from transcription_types import (
    DEFAULT_LANGUAGE,
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
        speech_session_factory=None,
        display_client_factory=None,
        clear_after_silence_seconds=1.0,
        debug=False,
    ):
        self.store = store
        self.display_url = display_url
        self.token = token
        self.broadcaster = broadcaster or EventBroadcaster()
        self.speech_session_factory = speech_session_factory or (lambda: MacOSSpeechSession())
        self.display_client_factory = display_client_factory or (
            lambda: DisplayUpdateClient(
                url=self.display_url,
                token=self.token,
                device="clear-oled-transcription-web",
            )
        )
        self.debug = debug
        self.clear_after_silence_seconds = max(0.0, float(clear_after_silence_seconds))
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
        self.speech_session = None
        self.session_task = None
        self.stop_event = None
        self.display_client = None
        self.last_final_text = ""
        self.clear_task = None
        self.post_clear_baseline_text = ""

    async def startup(self):
        self.store.mark_interrupted_sessions()

    async def shutdown(self):
        await self.stop_session()

    def list_audio_devices(self):
        try:
            return list_audio_devices()
        except Exception:
            return [
                {
                    "id": "",
                    "name": "System default microphone",
                    "is_default": True,
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                }
            ]

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
            "engine_backend": self.speech_session.backend_name if self.speech_session else None,
            "engine_model": self.speech_session.model_name if self.speech_session else None,
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
            self.current_oled_text = ""
            self.last_final_text = ""
            self.post_clear_baseline_text = ""
            self.display_state = DISPLAY_STATE_UNKNOWN
            self.display_connected = None
            self._cancel_clear_task()
            await self.publish_status()

            self.speech_session = self.speech_session_factory()
            try:
                await self.speech_session.start(device_id=device_id)
            except MacOSSpeechPermissionError as exc:
                self.service_state = SERVICE_STATE_IDLE
                self.mic_state = MIC_STATE_PERMISSION_DENIED
                self.last_error = str(exc)
                self.speech_session = None
                await self.publish_status()
                raise
            except (MacOSSpeechHelperError, OSError, RuntimeError) as exc:
                self.service_state = SERVICE_STATE_IDLE
                self.mic_state = MIC_STATE_ERROR
                self.last_error = str(exc)
                self.speech_session = None
                await self.publish_status()
                raise

            device_name = self._resolve_device_name(device_id)
            self.display_client = self.display_client_factory()
            self.active_session = self.store.create_session(
                device_name=device_name,
                model_name=self.speech_session.model_name,
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
            if self.speech_session is not None:
                await self.speech_session.stop()
            self._cancel_clear_task()
            await self.publish_status()

        await task
        return True

    def _resolve_device_name(self, device_id):
        devices = self.list_audio_devices()
        for device in devices:
            if device["id"] == str(device_id):
                return device["name"]
        for device in devices:
            if device.get("is_default"):
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
                last_error=self.active_session.last_error,
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
            if self.active_session is not None:
                self.active_session = self.store.update_session(
                    self.active_session.id,
                    display_connected=True,
                    last_error=self.active_session.last_error,
                )
        except (DisplayConnectionError, Exception) as exc:
            self.display_state = DISPLAY_STATE_DISCONNECTED
            self.display_connected = False
            self.last_display_error = str(exc)
            if self.active_session is not None:
                self.active_session = self.store.update_session(
                    self.active_session.id,
                    display_connected=False,
                    last_error=self.active_session.last_error,
                )
            await self.publish_status()

    async def _update_oled_text(self, text):
        normalized = normalize_transcript_text(text)
        if not normalized or normalized == self.current_oled_text:
            return
        await self._send_display_text(normalized)

    async def _clear_display(self):
        if self.display_client is None:
            return
        cleared_text = self.current_partial or self.last_final_text or self.current_oled_text
        try:
            await self.display_client.clear()
            self.display_state = DISPLAY_STATE_CONNECTED
            self.display_connected = True
            self.last_display_error = None
            self.post_clear_baseline_text = normalize_transcript_text(cleared_text)
            self.current_partial = ""
            self.current_oled_text = ""
            self.last_final_text = ""
            if self.active_session is not None:
                self.active_session = self.store.update_session(
                    self.active_session.id,
                    display_connected=True,
                    last_error=self.active_session.last_error,
                )
        except (DisplayConnectionError, Exception) as exc:
            self.display_state = DISPLAY_STATE_DISCONNECTED
            self.display_connected = False
            self.last_display_error = str(exc)
            if self.active_session is not None:
                self.active_session = self.store.update_session(
                    self.active_session.id,
                    display_connected=False,
                    last_error=self.active_session.last_error,
                )
        await self.publish_status()

    def _cancel_clear_task(self):
        if self.clear_task is None:
            return
        self.clear_task.cancel()
        self.clear_task = None

    def _schedule_clear_after_silence(self):
        self._cancel_clear_task()
        if self.clear_after_silence_seconds <= 0:
            return
        self.clear_task = asyncio.create_task(self._clear_after_silence())

    async def _clear_after_silence(self):
        try:
            await asyncio.sleep(self.clear_after_silence_seconds)
            await self._clear_display()
        except asyncio.CancelledError:
            raise
        finally:
            if asyncio.current_task() is self.clear_task:
                self.clear_task = None

    def _normalize_live_text(self, text):
        return normalize_transcript_text(text)

    @staticmethod
    def _find_overlap_prefix_length(baseline_tokens, incoming_tokens):
        if not baseline_tokens or not incoming_tokens:
            return 0

        max_prefix = min(len(baseline_tokens), len(incoming_tokens))
        for prefix_length in range(max_prefix, 0, -1):
            prefix = incoming_tokens[:prefix_length]
            last_start = len(baseline_tokens) - prefix_length
            for start in range(last_start + 1):
                if baseline_tokens[start : start + prefix_length] == prefix:
                    return prefix_length
        return 0

    def _trim_post_clear_oled_text(self, raw_text, *, is_final=False):
        normalized = normalize_transcript_text(raw_text)
        if not normalized:
            return ""

        baseline = self.post_clear_baseline_text
        if not baseline:
            return normalized

        baseline_tokens = baseline.split()
        incoming_tokens = normalized.split()
        overlap_prefix_length = self._find_overlap_prefix_length(baseline_tokens, incoming_tokens)
        if overlap_prefix_length == 0:
            self.post_clear_baseline_text = ""
            return normalized

        trimmed = " ".join(incoming_tokens[overlap_prefix_length:])
        if is_final and trimmed:
            self.post_clear_baseline_text = ""
        return trimmed

    async def _handle_partial(self, session_id, text, *, event_time=None):
        normalized = self._normalize_live_text(text)
        if not normalized:
            return

        oled_source_text = self._trim_post_clear_oled_text(normalized)
        oled_text = fit_transcript_tail_text(oled_source_text)
        if normalized == self.current_partial and oled_text == self.current_oled_text:
            return
        self.current_partial = normalized
        self._schedule_clear_after_silence()
        await self._update_oled_text(oled_text)
        timestamp = event_time or now_utc()
        await self.broadcaster.publish(
            "partial",
            {
                "session_id": session_id,
                "started_at_utc": format_utc(timestamp),
                "ended_at_utc": format_utc(timestamp),
                "text": normalized,
                "oled_text": oled_text,
            },
        )
        await self.publish_status()

    async def _handle_final(self, session_id, text, *, event_time=None):
        normalized = self._normalize_live_text(text)
        self.current_partial = ""
        if not normalized:
            await self.publish_status()
            return

        oled_source_text = self._trim_post_clear_oled_text(normalized, is_final=True)
        oled_text = fit_transcript_tail_text(oled_source_text)
        if normalized == self.last_final_text and oled_text == self.current_oled_text:
            await self.publish_status()
            return

        self.last_final_text = normalized
        timestamp = event_time or now_utc()
        try:
            segment = self.store.create_segment(
                session_id=session_id,
                started_at=timestamp,
                ended_at=timestamp,
                text=normalized,
                oled_text=oled_text,
                created_at=timestamp,
            )
        except Exception as exc:
            self.last_error = f"Transcript storage degraded: {exc}"
            await self.publish_status()
            segment = None

        self._schedule_clear_after_silence()
        await self._update_oled_text(oled_text)
        if segment is not None:
            await self.broadcaster.publish("final_segment", segment.to_api_dict())
        await self.publish_status()

    async def _run_session(self, session_id):
        session_error = None

        try:
            while True:
                event = await self.speech_session.read_event()
                if event is None:
                    if self.stop_event is not None and self.stop_event.is_set():
                        break
                    session_error = self.speech_session.last_error or "Speech helper exited unexpectedly."
                    break

                event_type = event.get("type")
                if event_type == "ready":
                    continue
                if event_type == "partial":
                    await self._handle_partial(session_id, event.get("text", ""))
                    continue
                if event_type == "final":
                    await self._handle_final(session_id, event.get("text", ""))
                    continue
                if event_type == "error":
                    session_error = event.get("message") or self.speech_session.last_error or "Speech helper failed."
                    if event.get("code") == "permission_denied":
                        self.mic_state = MIC_STATE_PERMISSION_DENIED
                    else:
                        self.mic_state = MIC_STATE_ERROR
                    self.last_error = session_error
                    await self.broadcaster.publish("error", {"message": session_error, "session_id": session_id})
                    break
        except Exception as exc:
            session_error = str(exc)
            self.last_error = session_error
            self.mic_state = MIC_STATE_ERROR
            await self.broadcaster.publish("error", {"message": session_error, "session_id": session_id})
        finally:
            self._cancel_clear_task()
            if self.speech_session is not None:
                with contextlib.suppress(Exception):
                    await self.speech_session.stop()
            if self.display_client is not None:
                with contextlib.suppress(Exception):
                    await self.display_client.close()

            if self.active_session and self.active_session.id == session_id:
                if session_error and not (self.stop_event and self.stop_event.is_set()):
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
                self.mic_state = MIC_STATE_IDLE if not session_error else self.mic_state
                self.current_partial = ""
                self.current_oled_text = ""
                self.last_final_text = ""
                self.post_clear_baseline_text = ""
                self.session_task = None
                self.stop_event = None
                self.display_client = None
                self.speech_session = None
                self.active_session = None
                await self.broadcaster.publish("session_stopped", stopped_session.to_api_dict())
                await self.publish_status()
