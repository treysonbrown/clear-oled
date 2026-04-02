#!/usr/bin/env python3

from __future__ import annotations

import tempfile
import wave

from transcription_types import DEFAULT_LANGUAGE, DEFAULT_MODEL, normalize_transcript_text


class TranscriptionEngineError(RuntimeError):
    pass


def write_pcm16_wav(path, audio_bytes, sample_rate=16000):
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(audio_bytes)


class MlxWhisperAdapter:
    backend_name = "mlx-whisper"

    def __init__(self, model_name):
        try:
            import mlx_whisper
        except ImportError as exc:
            raise TranscriptionEngineError("mlx-whisper is not installed.") from exc
        self.model_name = model_name or DEFAULT_MODEL
        self.module = mlx_whisper

    def transcribe_pcm16(self, audio_bytes, *, sample_rate=16000, language=DEFAULT_LANGUAGE):
        with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
            write_pcm16_wav(handle.name, audio_bytes, sample_rate=sample_rate)
            result = self.module.transcribe(
                handle.name,
                path_or_hf_repo=self.model_name,
                language=language,
            )
        return normalize_transcript_text((result or {}).get("text", ""))


class FasterWhisperAdapter:
    backend_name = "faster-whisper"

    def __init__(self, model_name):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionEngineError("faster-whisper is not installed.") from exc
        fallback_model = model_name or "tiny.en"
        if fallback_model.startswith("mlx-community/"):
            fallback_model = fallback_model.rsplit("/", 1)[-1]
        self.model = WhisperModel(fallback_model)

    def transcribe_pcm16(self, audio_bytes, *, sample_rate=16000, language=DEFAULT_LANGUAGE):
        with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
            write_pcm16_wav(handle.name, audio_bytes, sample_rate=sample_rate)
            segments, _ = self.model.transcribe(
                handle.name,
                language=language,
                condition_on_previous_text=False,
                vad_filter=False,
            )
            text = " ".join(segment.text.strip() for segment in segments if getattr(segment, "text", "").strip())
        return normalize_transcript_text(text)


class WhisperTranscriptionEngine:
    def __init__(self, *, model_name=DEFAULT_MODEL, language=DEFAULT_LANGUAGE):
        self.language = language or DEFAULT_LANGUAGE
        self.model_name = model_name or DEFAULT_MODEL
        self.adapter = None
        errors = []

        for adapter_class in (MlxWhisperAdapter, FasterWhisperAdapter):
            try:
                self.adapter = adapter_class(self.model_name)
                break
            except TranscriptionEngineError as exc:
                errors.append(str(exc))

        if self.adapter is None:
            details = "; ".join(errors) or "No supported Whisper backend is installed."
            raise TranscriptionEngineError(details)

    @property
    def backend_name(self):
        return self.adapter.backend_name

    def transcribe_pcm16(self, audio_bytes, *, sample_rate=16000):
        if not audio_bytes:
            return ""
        return self.adapter.transcribe_pcm16(
            audio_bytes,
            sample_rate=sample_rate,
            language=self.language,
        )
