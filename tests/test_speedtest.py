"""Tests for speedtest.py — run with: python -m unittest -v tests.test_speedtest"""
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import speedtest

PAYLOAD = os.urandom(3_000_000)  # 3 MB, hand-sized fixture


class _QuietHandler(BaseHTTPRequestHandler):
    """Local test server: /file (payload), /redir (302 -> /file), /slow (2s delay)."""

    seen_headers = {}

    def log_message(self, fmt, *args):
        pass  # keep unittest output pristine

    def do_GET(self):
        if self.path == "/redir":
            self.send_response(302)
            self.send_header("Location", "/file")
            self.end_headers()
            return
        if self.path == "/slow":
            time.sleep(2)
        if self.path in ("/file", "/slow"):
            _QuietHandler.seen_headers[self.path] = dict(self.headers)
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)
            return
        if self.path == "/truncated":
            # заявляем 5 МБ, отдаём 1 МБ и закрываем соединение — зеркало «закашлялось»
            self.send_response(200)
            self.send_header("Content-Length", str(5_000_000))
            self.end_headers()
            self.wfile.write(PAYLOAD[:1_000_000])
            self.close_connection = True
            return
        self.send_response(404)
        self.end_headers()


class _LocalServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()


class FetchOnceTest(_LocalServerCase):
    def test_counts_exact_bytes_and_time(self):
        # break: a wrong byte tally (e.g. only last chunk counted) or a non-positive timer
        total, elapsed = speedtest.fetch_once(self.base + "/file", 10)
        self.assertEqual(total, 3_000_000)
        self.assertGreater(elapsed, 0)

    def test_follows_redirect(self):
        # break: redirect handling disabled -> body of 302 (0 bytes) instead of payload
        total, _ = speedtest.fetch_once(self.base + "/redir", 10)
        self.assertEqual(total, 3_000_000)

    def test_sends_headers(self):
        # break: HEADERS dict lost from the Request -> server sees default UA, no Cache-Control
        speedtest.fetch_once(self.base + "/file", 10)
        headers = _QuietHandler.seen_headers["/file"]
        self.assertIn("speedtest", headers["User-Agent"].lower())
        self.assertEqual(headers.get("Cache-Control"), "no-cache")

    def test_no_files_written(self):
        # break: implementation spooling the response to disk
        before = sorted(os.listdir(os.getcwd()))
        speedtest.fetch_once(self.base + "/file", 10)
        self.assertEqual(sorted(os.listdir(os.getcwd())), before)

    def test_timeout_raises(self):
        # break: --timeout not passed to the socket -> 2s slow response would succeed
        with self.assertRaises(OSError):
            speedtest.fetch_once(self.base + "/slow", 0.5)

    def test_truncated_body_raises(self):
        # break: read loop trusting a premature close -> silent undercount reported as success
        with self.assertRaises(OSError):
            speedtest.fetch_once(self.base + "/truncated", 10)


class FormattingTest(unittest.TestCase):
    def test_format_size_megabytes_decimal(self):
        # break: binary MiB instead of decimal MB, or wrong rounding
        self.assertEqual(speedtest.format_size(792_723_456), "792.7 MB")
        self.assertEqual(speedtest.format_size(1_500_000), "1.5 MB")
        self.assertEqual(speedtest.format_size(0), "0.0 MB")

    def test_format_bitrate_si_scaling(self):
        # break: wrong thresholds (1024 instead of 1000) or wrong unit labels
        self.assertEqual(speedtest.format_bitrate(94_000_000), "94.0 Mbit/s")
        self.assertEqual(speedtest.format_bitrate(1_500_000_000), "1.5 Gbit/s")
        self.assertEqual(speedtest.format_bitrate(850_000), "850.0 kbit/s")
        self.assertEqual(speedtest.format_bitrate(500), "500.0 bit/s")


SCRIPT = os.path.join(os.path.dirname(__file__), "..", "speedtest.py")


def _cli(*args):
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, SCRIPT, *args], capture_output=True, text=True, timeout=60
    )


class CliTest(unittest.TestCase):
    def _run(self, *args):
        return _cli(*args)

    def test_bad_host_exits_1(self):
        # break: unhandled URLError -> traceback + exit != 1, or silent exit 0
        result = self._run("http://127.0.0.1:1/nope", "-n", "1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("speedtest:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_zero_requests_usage_error(self):
        # break: missing >=1 validation on -n -> script starts and fails at request 1
        result = self._run("http://127.0.0.1:1/x", "-n", "0")
        self.assertEqual(result.returncode, 2)

    def test_missing_url_usage_error(self):
        # break: url made optional -> script runs with no target
        result = self._run()
        self.assertEqual(result.returncode, 2)

    def test_bad_timeout_usage_error(self):
        # break: float() accepts anything -> --timeout -5 crashes with a traceback, not a usage error
        neg = self._run("http://127.0.0.1:1/x", "--timeout", "-5")
        self.assertEqual(neg.returncode, 2)
        self.assertNotIn("Traceback", neg.stderr)
        zero = self._run("http://127.0.0.1:1/x", "--timeout", "0")
        self.assertEqual(zero.returncode, 2)


class CliAgainstLocalServerTest(_LocalServerCase):
    """Characterization: locks the whole success-path and 404 contract through the real CLI."""

    def test_success_path_summary(self):
        # break: dropped summary section or non-zero exit on a good run
        result = _cli(self.base + "/file", "-n", "2")
        self.assertEqual(result.returncode, 0)
        self.assertIn("[1/2]", result.stdout)
        self.assertIn("[2/2]", result.stdout)
        self.assertIn("Среднее время запроса:", result.stdout)
        self.assertIn("Всего скачано: 6.0 MB", result.stdout)
        self.assertIn("Средняя скорость:", result.stdout)
        self.assertIn("bit/s", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_http_404_clean_error(self):
        # break: HTTPError not in the except chain -> traceback leaks to the user
        result = _cli(self.base + "/missing", "-n", "1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("speedtest:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
