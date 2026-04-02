#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

from transcription_types import DEFAULT_MODEL


REPO_ROOT = Path(__file__).resolve().parent
HELPER_PATH = REPO_ROOT / "macos_speech_helper.swift"


class MacOSSpeechHelperError(RuntimeError):
    pass


class MacOSSpeechPermissionError(MacOSSpeechHelperError):
    pass


class MacOSSpeechSession:
    backend_name = "macos-speech"

    def __init__(self, *, helper_path=HELPER_PATH, swift_binary=None):
        self.helper_path = Path(helper_path)
        self.swift_binary = swift_binary or shutil.which("swift")
        self.model_name = DEFAULT_MODEL
        self.process = None
        self._stderr_task = None
        self._stderr_lines = []

    @property
    def last_error(self):
        if not self._stderr_lines:
            return None
        return "\n".join(self._stderr_lines[-10:])

    async def start(self, *, device_id=None):
        if sys.platform != "darwin":
            raise MacOSSpeechHelperError("The macOS speech helper only runs on macOS.")
        if self.swift_binary is None:
            raise MacOSSpeechHelperError("The `swift` command is required to run the macOS speech helper.")
        if not self.helper_path.exists():
            raise MacOSSpeechHelperError(f"Speech helper not found at {self.helper_path}.")

        command = [self.swift_binary, str(self.helper_path)]
        if device_id not in (None, ""):
            command.extend(["--device-id", str(device_id)])

        self.process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr_task = asyncio.create_task(self._pump_stderr())

        event = await self._read_event(timeout=20.0)
        if event is None:
            raise MacOSSpeechHelperError(self.last_error or "Speech helper exited before it became ready.")
        if event.get("type") == "error":
            code = event.get("code")
            message = event.get("message") or self.last_error or "Speech helper failed to start."
            await self.stop()
            if code == "permission_denied":
                raise MacOSSpeechPermissionError(message)
            raise MacOSSpeechHelperError(message)
        if event.get("type") != "ready":
            raise MacOSSpeechHelperError(f"Unexpected speech helper bootstrap event: {event.get('type')}")

    async def _pump_stderr(self):
        try:
            while self.process is not None and self.process.stderr is not None:
                line = await self.process.stderr.readline()
                if not line:
                    break
                self._stderr_lines.append(line.decode("utf-8", errors="replace").rstrip())
                if len(self._stderr_lines) > 50:
                    self._stderr_lines = self._stderr_lines[-50:]
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _read_event(self, *, timeout=None):
        if self.process is None or self.process.stdout is None:
            return None
        try:
            if timeout is None:
                raw_line = await self.process.stdout.readline()
            else:
                raw_line = await asyncio.wait_for(self.process.stdout.readline(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise MacOSSpeechHelperError("Timed out waiting for the macOS speech helper to respond.") from exc

        if not raw_line:
            return None

        try:
            return json.loads(raw_line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise MacOSSpeechHelperError(f"Speech helper returned invalid JSON: {raw_line!r}") from exc

    async def read_event(self):
        return await self._read_event()

    async def stop(self):
        if self.process is None:
            return

        try:
            if self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
        finally:
            if self._stderr_task is not None:
                self._stderr_task.cancel()
                try:
                    await self._stderr_task
                except asyncio.CancelledError:
                    pass
            self.process = None
            self._stderr_task = None
