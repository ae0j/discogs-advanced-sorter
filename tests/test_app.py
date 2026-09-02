import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from app import app, price_to_number


class AppTests(unittest.TestCase):
    def setUp(self):
        app_module._in_flight_tasks.clear()

    def test_price_sort_parser_handles_common_formats(self):
        self.assertEqual(price_to_number("$1,234.56"), 1234.56)
        self.assertEqual(price_to_number("€1.234,56"), 1234.56)
        self.assertEqual(price_to_number("SEK 299.00"), 299.0)

    @patch("app.threading.Thread.start", return_value=None)
    def test_current_filtered_url_starts_background_task(self, _start):
        with app.test_client() as client:
            response = client.post(
                "/",
                data={
                    "user_input": "",
                    "filtered_url": "https://www.discogs.com/shop/list/?seller=2372731",
                },
            )
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertIn("unique_id", payload)

    @patch("app.threading.Thread.start", return_value=None)
    def test_duplicate_active_search_reuses_one_task(self, start):
        with app.test_client() as client:
            first = client.post("/", data={"user_input": "CD-Record-Sale"})
            second = client.post("/", data={"user_input": "cd-record-sale"})
        self.assertEqual(
            first.get_json()["unique_id"], second.get_json()["unique_id"]
        )
        self.assertEqual(start.call_count, 1)

    @patch("app.threading.Thread.start", return_value=None)
    def test_task_status_exposes_progress_fields(self, _start):
        with app.test_client() as client:
            created = client.post("/", data={"user_input": "koreadisco"})
            task_id = created.get_json()["unique_id"]
            status = client.get(f"/task_status/{task_id}").get_json()
        self.assertEqual(status["processed"], 0)
        self.assertIsNone(status["total"])
        self.assertEqual(status["phase"], "queued")
        self.assertGreaterEqual(status["elapsed"], 0)
        self.assertGreaterEqual(status["idle_for"], 0)

    @patch("app.os.path.exists", return_value=True)
    def test_completed_csv_survives_in_memory_status_loss(self, _exists):
        task_id = "5fdaadbe-48f7-4eb7-afd7-b11889649011"
        with app.test_client() as client:
            status = client.get(f"/task_status/{task_id}").get_json()
        self.assertTrue(status["completed"])
        self.assertIsNone(status["error"])

    def test_unrelated_discogs_url_fails_before_task_creation(self):
        with app.test_client() as client:
            response = client.post(
                "/",
                data={
                    "user_input": "",
                    "filtered_url": "https://www.discogs.com/release/1",
                },
            )
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertIn("/shop/list/", payload["message"])

    def test_frontend_renders_validation_messages_as_text(self):
        script = Path("static/main.js").read_text(encoding="utf-8")
        self.assertIn("messageNode.textContent = message || \"\";", script)
        self.assertNotIn("messageNode.innerHTML = message", script)


if __name__ == "__main__":
    unittest.main()
