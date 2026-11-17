import http.server
import os
import random
import threading
import unittest

from hamcrest import assert_that, close_to, contains_string, equal_to

from netspeed import Benchmark, Download, Measurement, Report


class Body(http.server.BaseHTTPRequestHandler):
    """
    Local HTTP handler that answers every GET with the same random body
    and counts how many requests it has served.
    """

    def do_GET(self):
        self.server.hits += 1
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.server.body)))
        self.end_headers()
        self.wfile.write(self.server.body)

    def log_message(self, *args):
        pass


class Server:
    """
    Temporary HTTP server on an ephemeral local port, serving one body.
    """

    def __init__(self, body: bytes):
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Body)
        self._httpd.body = body
        self._httpd.hits = 0

    def __enter__(self):
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *args):
        self._httpd.shutdown()
        self._httpd.server_close()

    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_port}/file.bin"

    def hits(self) -> int:
        return self._httpd.hits


class DownloadTest(unittest.TestCase):
    def test_counts_every_byte_of_the_body(self):
        size = random.randint(100_000, 900_000)
        with Server(os.urandom(size)) as server:
            assert_that(Download(server.url(), 5).sample()[1], equal_to(size), "download did not count the whole body")


class BenchmarkTest(unittest.TestCase):
    def test_sends_requested_number_of_requests(self):
        count = random.randint(2, 9)
        with Server(os.urandom(1024)) as server:
            Benchmark(Download(server.url(), 5), count).measurement()
            assert_that(server.hits(), equal_to(count), "benchmark did not send the requested number of requests")

    def test_sums_volume_of_all_requests(self):
        size = random.randint(1000, 50_000)
        with Server(os.urandom(size)) as server:
            assert_that(Benchmark(Download(server.url(), 5), 7).measurement().volume(), equal_to(size * 7), "benchmark did not sum the volume of all requests")

    def test_rejects_zero_requests(self):
        with self.assertRaises(ValueError, msg="benchmark did not reject zero requests"):
            Benchmark(Download("http://127.0.0.1:1/", 1), 0)


class MeasurementTest(unittest.TestCase):
    def test_averages_request_time(self):
        assert_that(Measurement(((0.25, 10), (0.75, 30))).average(), close_to(0.5, 1e-9), "measurement did not average request time")

    def test_computes_speed_in_megabits_per_second(self):
        assert_that(Measurement(((0.5, 1_250_000), (1.5, 3_750_000))).speed(), close_to(20.0, 1e-9), "measurement did not compute speed in megabits per second")

    def test_rejects_empty_samples(self):
        with self.assertRaises(ValueError, msg="measurement did not reject empty samples"):
            Measurement(())


class ReportTest(unittest.TestCase):
    def test_prints_speed_in_megabits_per_second(self):
        assert_that(Report(Measurement(((1.0, 2_500_000),))).text(), contains_string("Download speed: 20.00 Mbit/s"), "report did not print speed in megabits per second")


if __name__ == "__main__":
    unittest.main()
