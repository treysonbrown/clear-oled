import tempfile
import unittest
from pathlib import Path

from reminder_store import ReminderStore
from reminder_web_app import ReminderAppConfig, create_app

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    TestClient = None


@unittest.skipUnless(TestClient is not None, "fastapi is required for reminder web app tests.")
class ReminderWebAppTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "reminders.db"
        self.config = ReminderAppConfig(
            db_path=self.db_path,
            display_url="ws://127.0.0.1:8766",
            token="secret",
            display_seconds=20,
        )
        self.app = create_app(self.config, start_scheduler=False)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def create_payload(self, message="Take medicine", scheduled_at_local="2099-03-31T20:30"):
        return {
            "message": message,
            "scheduled_at_local": scheduled_at_local,
            "timezone": "America/Denver",
        }

    def test_root_route_serves_html(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Schedule messages for the OLED", response.text)

    def test_create_reminder_with_valid_local_datetime_succeeds(self):
        response = self.client.post("/api/reminders", json=self.create_payload())

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["message"], "Take medicine")
        self.assertEqual(body["status"], "scheduled")

    def test_create_reminder_with_past_datetime_returns_400(self):
        response = self.client.post(
            "/api/reminders",
            json=self.create_payload(scheduled_at_local="2000-01-01T00:00"),
        )

        self.assertEqual(response.status_code, 400)

    def test_create_reminder_with_empty_message_returns_400(self):
        response = self.client.post("/api/reminders", json=self.create_payload(message="   "))

        self.assertEqual(response.status_code, 400)

    def test_create_reminder_with_too_long_message_returns_400(self):
        response = self.client.post("/api/reminders", json=self.create_payload(message="x" * 121))

        self.assertEqual(response.status_code, 400)

    def test_patch_delivered_reminder_returns_409(self):
        create_response = self.client.post("/api/reminders", json=self.create_payload())
        reminder_id = create_response.json()["id"]

        store = ReminderStore(self.db_path)
        store.mark_delivering(reminder_id)
        store.mark_delivered(reminder_id)

        response = self.client.patch(
            f"/api/reminders/{reminder_id}",
            json=self.create_payload(message="updated", scheduled_at_local="2099-03-31T21:30"),
        )

        self.assertEqual(response.status_code, 409)

    def test_delete_scheduled_reminder_succeeds(self):
        create_response = self.client.post("/api/reminders", json=self.create_payload())
        reminder_id = create_response.json()["id"]

        response = self.client.delete(f"/api/reminders/{reminder_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "canceled")

    def test_list_endpoint_filters_by_status(self):
        delivered_response = self.client.post("/api/reminders", json=self.create_payload(message="delivered"))
        scheduled_response = self.client.post(
            "/api/reminders",
            json=self.create_payload(message="scheduled", scheduled_at_local="2099-03-31T21:30"),
        )
        delivered_id = delivered_response.json()["id"]
        scheduled_id = scheduled_response.json()["id"]

        store = ReminderStore(self.db_path)
        store.mark_delivering(delivered_id)
        store.mark_delivered(delivered_id)

        response = self.client.get("/api/reminders?status=delivered")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()], [delivered_id])
        self.assertNotIn(scheduled_id, [item["id"] for item in response.json()])


if __name__ == "__main__":
    unittest.main()
