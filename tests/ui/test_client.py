"""Contract and failure-path tests without network access."""

import io
import json
import socket
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from ui.client import ApiClient, ApiError, MAX_RESPONSE_BYTES


class Response(io.BytesIO):
    def __init__(self, body, content_type="application/json"):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}


def json_response(value):
    return Response(json.dumps(value).encode())


def record(**changes):
    result = {
        "run_id": "run-1", "issue_time": "2026-01-31T00:00:00Z",
        "turbine_id": "turbine_1", "valid_time": "2026-01-31T01:00:00Z",
        "lead_hours": 1, "prediction": 0.4, "target_unit": "normalized_power",
    }
    return {**result, **changes}


class ApiClientTests(unittest.TestCase):
    def setUp(self):
        self.client = ApiClient("http://localhost:8000/api/", timeout=4)

    @patch("ui.client.request.urlopen")
    def test_health_and_base_path(self, urlopen):
        urlopen.return_value = json_response({"status": "ok", "model_ready": True})
        self.assertTrue(self.client.health()["model_ready"])
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "http://localhost:8000/api/health")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 4)

    @patch("ui.client.request.urlopen")
    def test_create_normalizes_explicit_timezone_and_posts_json(self, urlopen):
        urlopen.return_value = json_response({"run_id": "run-1", "status": "queued"})
        self.client.create_run("2026-01-31T05:00:00+05:00", 48, ["turbine_1", "turbine_2"])
        req = urlopen.call_args.args[0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data), {
            "issue_time": "2026-01-31T00:00:00Z", "horizon_hours": 48,
            "turbine_ids": ["turbine_1", "turbine_2"],
        })

    @patch("ui.client.request.urlopen")
    def test_invalid_input_never_requests_network(self, urlopen):
        cases = [
            ("2026-01-31T00:00:00", 24, ["turbine_1"]),
            ("bad-date", 24, ["turbine_1"]),
            ("2026-01-31T00:30:00Z", 24, ["turbine_1"]),
            ("2026-01-31T00:00:00Z", 12, ["turbine_1"]),
            ("2026-01-31T00:00:00Z", 24.0, ["turbine_1"]),
            ("2026-01-31T00:00:00Z", True, ["turbine_1"]),
            ("2026-01-31T00:00:00Z", 24, []),
            ("2026-01-31T00:00:00Z", 24, ["turbine_1", "turbine_1"]),
            ("2026-01-31T00:00:00Z", 24, ["a\nb"]),
        ]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(ApiError) as error:
                self.client.create_run(*args)
            self.assertEqual(error.exception.kind, "input")
        urlopen.assert_not_called()

    def test_invalid_base_urls_and_timeouts(self):
        for value in ("", "file:///tmp/a", "ftp://host", "https://user:pass@host", "http://host?q=x", "http://host/#x", "http://host:999999", "http://a b", "http://host:0"):
            with self.subTest(value=value), self.assertRaises(ApiError):
                ApiClient(value)
        for timeout in (0, -1, 61, True, "3", float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ApiError):
                ApiClient("http://localhost:8000", timeout=timeout)

    @patch("ui.client.request.urlopen")
    def test_run_identifier_is_quoted(self, urlopen):
        urlopen.return_value = json_response({"run_id": "a/b?x#y", "status": "completed"})
        self.client.get_run("a/b?x#y")
        self.assertEqual(urlopen.call_args.args[0].full_url, "http://localhost:8000/api/runs/a%2Fb%3Fx%23y")
        for value in ("", ".", "..", "a\nb", None):
            with self.subTest(value=value), self.assertRaises(ApiError):
                self.client.get_run(value)

    @patch("ui.client.request.urlopen")
    def test_timeout_and_connection_have_explicit_error_kinds(self, urlopen):
        for failure, kind in ((socket.timeout(), "timeout"), (URLError(socket.timeout()), "timeout"), (URLError("offline"), "connection"), (OSError("closed"), "connection")):
            urlopen.side_effect = failure
            with self.subTest(failure=failure), self.assertRaises(ApiError) as error:
                self.client.health()
            self.assertEqual(error.exception.kind, kind)

    @patch("ui.client.request.urlopen")
    def test_http_error_preserves_status_and_json_detail(self, urlopen):
        urlopen.side_effect = HTTPError("http://localhost", 503, "unavailable", {}, io.BytesIO(b'{"detail":"Model is not ready"}'))
        with self.assertRaises(ApiError) as error:
            self.client.health()
        self.assertEqual(error.exception.status_code, 503)
        self.assertIn("Model is not ready", str(error.exception))

    @patch("ui.client.request.urlopen")
    def test_malformed_json_and_wrong_shapes_fail(self, urlopen):
        for raw in (b"not json", b"\xff", b'{"status":NaN}', b"[]", b"{}"):
            urlopen.return_value = Response(raw)
            with self.subTest(raw=raw), self.assertRaises(ApiError):
                self.client.health()
        for value in ({"status": "queued"}, {"run_id": "r", "status": "mystery"}, {"run_id": "r", "status": ["queued"]}):
            urlopen.return_value = json_response(value)
            with self.assertRaises(ApiError):
                self.client.get_run("r")

    @patch("ui.client.request.urlopen")
    def test_forecast_array_envelope_and_empty(self, urlopen):
        for payload, expected in (([record()], [record()]), ({"records": [record()]}, [record()]), ([], [])):
            urlopen.return_value = json_response(payload)
            self.assertEqual(self.client.get_forecast("run-1"), expected)
        for payload in ({"data": [record()]}, {"records": {}}, ["row"], [{}], [record(prediction="0.5")], [record(prediction=True)]):
            urlopen.return_value = json_response(payload)
            with self.subTest(payload=payload), self.assertRaises(ApiError):
                self.client.get_forecast("run-1")

    @patch("ui.client.request.urlopen")
    def test_cross_run_status_or_forecast_is_rejected(self, urlopen):
        urlopen.return_value = json_response({"run_id": "other-run", "status": "completed"})
        with self.assertRaises(ApiError) as error:
            self.client.get_run("run-1")
        self.assertEqual(error.exception.kind, "response")
        for payload in ([record(run_id="other-run")], [record(), record(run_id="other-run")], {"records": [record(run_id="other-run")]}):
            urlopen.return_value = json_response(payload)
            with self.subTest(payload=payload), self.assertRaises(ApiError) as error:
                self.client.get_forecast("run-1")
            self.assertEqual(error.exception.kind, "response")

    @patch("ui.client.request.urlopen")
    def test_csv_keeps_bytes_and_rejects_empty_or_error_document(self, urlopen):
        raw = b"run_id,prediction,source_mode\nrun-1,0.2,real\n"
        urlopen.return_value = Response(raw, "text/csv; charset=utf-8")
        self.assertEqual(self.client.get_forecast_csv("run-1"), raw)
        self.assertTrue(urlopen.call_args.args[0].full_url.endswith("/runs/run-1/forecast.csv"))
        for body, content_type in ((b"  \n", "text/csv"), (b"<html>login</html>", "text/html"), (b'{"detail":"not ready"}', "application/json")):
            urlopen.return_value = Response(body, content_type)
            with self.subTest(content_type=content_type), self.assertRaises(ApiError):
                self.client.get_forecast_csv("run-1")

    @patch("ui.client.request.urlopen")
    def test_evaluation_unavailable_is_preserved(self, urlopen):
        data = {"status": "unavailable", "test_truth_available": False, "metrics": None}
        urlopen.return_value = json_response(data)
        self.assertEqual(self.client.get_evaluation(), data)
        urlopen.return_value = json_response([])
        with self.assertRaises(ApiError):
            self.client.get_evaluation()

    @patch("ui.client.request.urlopen")
    def test_response_read_is_bounded(self, urlopen):
        urlopen.return_value = Response(b"x" * (MAX_RESPONSE_BYTES + 2))
        with self.assertRaises(ApiError) as error:
            self.client.get_forecast_csv("run-1")
        self.assertIn("10 MiB", str(error.exception))


if __name__ == "__main__":
    unittest.main()
