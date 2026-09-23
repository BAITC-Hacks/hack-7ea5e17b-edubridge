"""Exercise real local HTTP transport against an explicitly synthetic fixture.

This is a UI contract test, not a product backend or evidence of model quality.
No external network service is contacted.
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from ui.client import ApiClient, ApiError
from ui.fixtures import DemoClient
from ui.presentation import ForecastError, validate_csv, validate_forecast, validate_run_identity


class LocalHttpIntegrationTests(unittest.TestCase):
    def test_synthetic_contract_over_real_http_and_malformed_csv(self):
        backend = DemoClient()
        received_posts = []
        routes = []
        controls = {"malformed_csv": False}

        class FixtureHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def send_body(self, value, status=200, content_type="application/json"):
                body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                routes.append(("POST", self.path))
                if self.path != "/runs":
                    self.send_body({"detail": "Unknown test route"}, status=404)
                    return
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received_posts.append(payload)
                self.send_body(backend.create_run(**payload), status=202)

            def do_GET(self):
                routes.append(("GET", self.path))
                path = urlsplit(self.path).path
                try:
                    if path == "/health":
                        self.send_body(backend.health())
                    elif path == "/evaluation":
                        self.send_body(backend.get_evaluation())
                    elif path.startswith("/runs/"):
                        parts = path.split("/")
                        run_id = unquote(parts[2])
                        if len(parts) == 3:
                            self.send_body(backend.get_run(run_id))
                        elif len(parts) == 4 and parts[3] == "forecast":
                            self.send_body(backend.get_forecast(run_id))
                        elif len(parts) == 4 and parts[3] == "forecast.csv":
                            content = (
                                b"run_id,prediction\nwrong-run,0.2\n"
                                if controls["malformed_csv"] else backend.get_forecast_csv(run_id)
                            )
                            self.send_body(content, content_type="text/csv; charset=utf-8")
                        else:
                            self.send_body({"detail": "Unknown test route"}, status=404)
                    else:
                        self.send_body({"detail": "Unknown test route"}, status=404)
                except ApiError as exc:
                    self.send_body({"detail": str(exc)}, status=exc.status_code or 400)

        server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()
        try:
            client = ApiClient(f"http://127.0.0.1:{server.server_port}", timeout=2)
            health = client.health()
            self.assertEqual(health["source_mode"], "synthetic")
            self.assertTrue(health["demo_ready"])
            self.assertFalse(health["model_ready"])

            run = client.create_run("2026-02-01T00:00:00+05:00", 48, ["turbine_1", "turbine_2"])
            self.assertEqual(run["status"], "queued")
            self.assertEqual(received_posts, [{
                "issue_time": "2026-01-31T19:00:00Z",
                "horizon_hours": 48,
                "turbine_ids": ["turbine_1", "turbine_2"],
            }])

            statuses = []
            for _ in range(8):
                run = client.get_run(run["run_id"])
                statuses.append(run["status"])
                if run["status"] == "completed":
                    break
            self.assertEqual(statuses, ["fetching_weather", "validating", "forecasting", "analysing", "completed"])
            self.assertIn("Синтетическая демонстрация", run["warnings"][0])

            records = client.get_forecast(run["run_id"])
            rows = validate_forecast(records, received_posts[0])
            validate_run_identity(rows, run)
            self.assertEqual(len(rows), 96)
            self.assertTrue(all(row["source_mode"] == "synthetic" for row in rows))
            content = client.get_forecast_csv(run["run_id"])
            self.assertEqual(validate_csv(content, rows, run), content)
            self.assertIn("Синтетическая демонстрация", content.decode("utf-8"))

            evaluation = client.get_evaluation()
            self.assertEqual(evaluation["status"], "unavailable")
            self.assertFalse(evaluation["test_truth_available"])
            self.assertIsNone(evaluation["metrics"])

            with self.assertRaises(ApiError) as error:
                client.get_run("missing-run")
            self.assertEqual(error.exception.status_code, 404)

            controls["malformed_csv"] = True
            malformed = client.get_forecast_csv(run["run_id"])
            with self.assertRaises(ForecastError):
                validate_csv(malformed, rows, run)

            expected_routes = {
                ("GET", "/health"), ("POST", "/runs"),
                ("GET", f"/runs/{run['run_id']}"),
                ("GET", f"/runs/{run['run_id']}/forecast"),
                ("GET", f"/runs/{run['run_id']}/forecast.csv"),
                ("GET", "/evaluation"),
            }
            self.assertTrue(expected_routes.issubset(set(routes)))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
