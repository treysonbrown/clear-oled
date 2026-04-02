import asyncio
import tempfile
import unittest
from pathlib import Path

from transcription_web_app import TranscriptionAppConfig, create_app

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    TestClient = None


class FakeBroadcaster:
    def __init__(self):
        self.queue = asyncio.Queue()

    async def subscribe(self):
        return self.queue

    async def unsubscribe(self, queue):
        return None


class FakeService:
    def __init__(self):
        self.broadcaster = FakeBroadcaster()
        self.started = False

    async def startup(self):
        return None

    async def shutdown(self):
        return None

    def status_snapshot(self):
        return {
            "service_state": "idle",
            "mic_state": "idle",
            "display_state": "connected",
            "display_connected": True,
            "last_error": None,
            "last_display_error": None,
            "current_partial": "",
            "current_oled_text": "",
            "current_session": None,
            "engine_backend": "macos-speech",
            "engine_model": "macos-speech",
        }

    def list_audio_devices(self):
        return [{"id": "0", "name": "Built-in Microphone", "is_default": True}]

    async def start_session(self, *, device_id=None, model=None):
        self.started = True
        return {"id": "session-1", "status": "running"}

    async def stop_session(self):
        self.started = False
        return True

    def list_sessions(self, *, limit=20):
        return [{"id": "session-1", "status": "stopped"}]

    def get_session_detail(self, session_id):
        return {
            "session": {"id": session_id, "status": "stopped"},
            "segments": [{"id": "segment-1", "text": "hello", "oled_text": "hello"}],
        }


@unittest.skipUnless(TestClient is not None, "fastapi is required for transcription web app tests.")
class TranscriptionWebAppTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        static_root = Path(self.tempdir.name) / "static"
        frontend_root = static_root / "transcription"
        frontend_root.mkdir(parents=True)
        (frontend_root / "index.html").write_text("<html><body>transcription console</body></html>", encoding="utf-8")
        self.config = TranscriptionAppConfig(
            db_path=Path(self.tempdir.name) / "transcription.db",
            display_url="ws://127.0.0.1:8766",
            token="secret",
            static_root=static_root,
            frontend_root=frontend_root,
        )
        self.service = FakeService()
        self.app = create_app(self.config, service=self.service)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_root_route_serves_frontend(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("transcription console", response.text)

    def test_status_and_control_routes(self):
        status = self.client.get("/api/status")
        start = self.client.post("/api/session/start", json={"device_id": "0"})
        stop = self.client.post("/api/session/stop")

        self.assertEqual(status.status_code, 200)
        self.assertEqual(start.status_code, 200)
        self.assertEqual(stop.status_code, 200)
        self.assertFalse(self.service.started)


if __name__ == "__main__":
    unittest.main()
