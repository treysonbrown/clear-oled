#!/usr/bin/env python3

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass

from transcription_types import now_utc

try:
    import sounddevice
except ModuleNotFoundError:
    sounddevice = None

try:
    import webrtcvad
except ModuleNotFoundError:
    webrtcvad = None


SAMPLE_RATE = 16000
CHANNELS = 1
FRAME_MS = 30
BYTES_PER_SAMPLE = 2
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * BYTES_PER_SAMPLE


class AudioDependencyError(RuntimeError):
    pass


class MicrophonePermissionError(RuntimeError):
    pass


class AudioDeviceError(RuntimeError):
    pass


def require_audio_dependencies():
    if sounddevice is None:
        raise AudioDependencyError(
            "The `sounddevice` package is required. Install it with `python3 -m pip install sounddevice`."
        )
    if webrtcvad is None:
        raise AudioDependencyError(
            "The `webrtcvad-wheels` package is required. Install it with `python3 -m pip install webrtcvad-wheels`."
        )


def list_audio_devices():
    if sounddevice is None:
        return [
            {
                "id": "",
                "name": "System default microphone",
                "is_default": True,
                "max_input_channels": 1,
                "default_samplerate": SAMPLE_RATE,
            }
        ]
    devices = []
    default_input, _ = sounddevice.default.device
    for index, device in enumerate(sounddevice.query_devices()):
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        devices.append(
            {
                "id": str(index),
                "name": device.get("name") or f"Input {index}",
                "is_default": index == default_input,
                "max_input_channels": int(device.get("max_input_channels", 0)),
                "default_samplerate": int(device.get("default_samplerate", SAMPLE_RATE)),
            }
        )
    return devices


@dataclass(frozen=True)
class PhraseEvent:
    type: str
    started_at: object
    ended_at: object
    audio_bytes: bytes


class AudioPhraseDetector:
    def __init__(
        self,
        *,
        sample_rate=SAMPLE_RATE,
        frame_ms=FRAME_MS,
        start_voiced_frames=8,
        end_silence_frames=27,
        partial_interval_frames=17,
        vad_aggressiveness=2,
        vad=None,
    ):
        if vad is None:
            require_audio_dependencies()
            vad = webrtcvad.Vad(vad_aggressiveness)
        self.vad = vad
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.start_voiced_frames = start_voiced_frames
        self.end_silence_frames = end_silence_frames
        self.partial_interval_frames = partial_interval_frames
        self.buffered_voiced = deque(maxlen=start_voiced_frames)
        self.in_phrase = False
        self.phrase_frames = []
        self.phrase_start = None
        self.speech_streak = 0
        self.silence_streak = 0
        self.frames_since_partial = 0

    def _is_speech(self, frame_bytes):
        return bool(self.vad.is_speech(frame_bytes, self.sample_rate))

    def feed(self, frame_bytes, frame_ended_at=None):
        if not frame_bytes:
            return []

        ended_at = frame_ended_at or now_utc()
        started_at = ended_at
        events = []
        is_speech = self._is_speech(frame_bytes)

        if not self.in_phrase:
            if is_speech:
                self.buffered_voiced.append((frame_bytes, started_at, ended_at))
                self.speech_streak += 1
                if self.speech_streak >= self.start_voiced_frames:
                    self.in_phrase = True
                    self.phrase_frames = list(self.buffered_voiced)
                    self.phrase_start = self.phrase_frames[0][1]
                    self.buffered_voiced.clear()
                    self.silence_streak = 0
                    self.frames_since_partial = 0
                    events.append(
                        PhraseEvent(
                            type="phrase_started",
                            started_at=self.phrase_start,
                            ended_at=ended_at,
                            audio_bytes=b"".join(frame for frame, _, _ in self.phrase_frames),
                        )
                    )
            else:
                self.buffered_voiced.clear()
                self.speech_streak = 0
            return events

        self.phrase_frames.append((frame_bytes, started_at, ended_at))
        self.frames_since_partial += 1
        if is_speech:
            self.silence_streak = 0
        else:
            self.silence_streak += 1

        phrase_audio = b"".join(frame for frame, _, _ in self.phrase_frames)
        if self.frames_since_partial >= self.partial_interval_frames:
            self.frames_since_partial = 0
            events.append(
                PhraseEvent(
                    type="partial_ready",
                    started_at=self.phrase_start,
                    ended_at=ended_at,
                    audio_bytes=phrase_audio,
                )
            )

        if self.silence_streak >= self.end_silence_frames:
            speech_frames = self.phrase_frames[:-self.silence_streak] if self.silence_streak else self.phrase_frames
            if speech_frames:
                final_ended_at = speech_frames[-1][2]
                events.append(
                    PhraseEvent(
                        type="phrase_finished",
                        started_at=self.phrase_start,
                        ended_at=final_ended_at,
                        audio_bytes=b"".join(frame for frame, _, _ in speech_frames),
                    )
                )
            self.reset()

        return events

    def flush(self, ended_at=None):
        if not self.in_phrase or not self.phrase_frames:
            self.reset()
            return None
        speech_frames = self.phrase_frames[:-self.silence_streak] if self.silence_streak else self.phrase_frames
        self.reset()
        if not speech_frames:
            return None
        return PhraseEvent(
            type="phrase_finished",
            started_at=speech_frames[0][1],
            ended_at=ended_at or speech_frames[-1][2],
            audio_bytes=b"".join(frame for frame, _, _ in speech_frames),
        )

    def reset(self):
        self.buffered_voiced.clear()
        self.in_phrase = False
        self.phrase_frames = []
        self.phrase_start = None
        self.speech_streak = 0
        self.silence_streak = 0
        self.frames_since_partial = 0


class MicrophoneInput:
    def __init__(
        self,
        *,
        device_id=None,
        sample_rate=SAMPLE_RATE,
        channels=CHANNELS,
        blocksize=FRAME_BYTES // BYTES_PER_SAMPLE,
    ):
        require_audio_dependencies()
        self.device_id = int(device_id) if device_id not in (None, "") else None
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize
        self.stream = None

    def start(self, *, loop, queue):
        def callback(indata, frames, time_info, status):
            payload = bytes(indata)
            try:
                loop.call_soon_threadsafe(queue.put_nowait, payload)
            except asyncio.QueueFull:
                pass

        try:
            self.stream = sounddevice.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=self.blocksize,
                channels=self.channels,
                dtype="int16",
                callback=callback,
                device=self.device_id,
            )
            self.stream.start()
        except Exception as exc:
            message = str(exc).lower()
            if "permission" in message or "not permitted" in message:
                raise MicrophonePermissionError(
                    "Microphone access is denied. Enable microphone permission for your terminal or Python app in "
                    "System Settings > Privacy & Security > Microphone, then restart the app."
                ) from exc
            raise AudioDeviceError(f"Unable to open the selected microphone: {exc}") from exc

    def stop(self):
        if self.stream is None:
            return
        try:
            self.stream.stop()
        finally:
            self.stream.close()
            self.stream = None
