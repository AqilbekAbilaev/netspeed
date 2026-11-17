import http.server
import io
import json
import os
import random
import threading
import unittest

from hamcrest import assert_that, close_to, contains_string, equal_to, is_not, less_than_or_equal_to

from netspeed import Benchmark, Console, Download, Measurement, Report, Sample


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


class Silence:
    """
    Progress that shows nothing, for runs where output does not matter.
    """

    def advance(self, done, total):
        pass

    def complete(self, label, sample):
        pass


class Journal:
    """
    Progress that remembers every event it receives, for inspection.
    """

    def __init__(self):
        self.advances = []
        self.labels = []

    def advance(self, done, total):
        self.advances.append((done, total))

    def complete(self, label, sample):
        self.labels.append(label)


class Terminal(io.StringIO):
    """
    In-memory text stream that claims to be an interactive terminal.
    """

    def isatty(self):
        return True


class DownloadTest(unittest.TestCase):
    def test_counts_every_byte_of_the_body(self):
        size = random.randint(100_000, 900_000)
        with Server(os.urandom(size)) as server:
            assert_that(Download(server.url(), 5).sample(Silence()).size, equal_to(size), "download did not count the whole body")

    def test_keeps_latency_within_request_time(self):
        with Server(os.urandom(random.randint(100_000, 900_000))) as server:
            sample = Download(server.url(), 5).sample(Silence())
            assert_that(sample.latency, less_than_or_equal_to(sample.seconds), "download did not keep latency within request time")

    def test_reports_progress_up_to_full_size(self):
        size = random.randint(100_000, 900_000)
        journal = Journal()
        with Server(os.urandom(size)) as server:
            Download(server.url(), 5).sample(journal)
        assert_that(journal.advances[-1], equal_to((size, size)), "download did not report progress up to full size")


class BenchmarkTest(unittest.TestCase):
    def test_sends_requested_number_of_requests(self):
        count = random.randint(2, 9)
        with Server(os.urandom(1024)) as server:
            Benchmark(Download(server.url(), 5), count, 0, Silence()).measurement()
            assert_that(server.hits(), equal_to(count), "benchmark did not send the requested number of requests")

    def test_sends_warmup_requests_in_addition(self):
        count = random.randint(2, 9)
        warmup = random.randint(1, 3)
        with Server(os.urandom(1024)) as server:
            Benchmark(Download(server.url(), 5), count, warmup, Silence()).measurement()
            assert_that(server.hits(), equal_to(count + warmup), "benchmark did not send warm-up requests in addition")

    def test_excludes_warmup_from_volume(self):
        size = random.randint(1000, 50_000)
        with Server(os.urandom(size)) as server:
            assert_that(Benchmark(Download(server.url(), 5), 4, 2, Silence()).measurement().volume(), equal_to(size * 4), "benchmark did not exclude warm-up from volume")

    def test_labels_every_completed_request(self):
        journal = Journal()
        with Server(os.urandom(1024)) as server:
            Benchmark(Download(server.url(), 5), 2, 1, journal).measurement()
        assert_that(journal.labels, equal_to(["Warm-up 1/1", "Request 1/2", "Request 2/2"]), "benchmark did not label every completed request")

    def test_rejects_zero_requests(self):
        with self.assertRaises(ValueError, msg="benchmark did not reject zero requests"):
            Benchmark(Download("http://127.0.0.1:1/", 1), 0, 0, Silence())

    def test_rejects_negative_warmup(self):
        with self.assertRaises(ValueError, msg="benchmark did not reject negative warm-up"):
            Benchmark(Download("http://127.0.0.1:1/", 1), 3, -1, Silence())


class MeasurementTest(unittest.TestCase):
    def test_averages_request_time(self):
        assert_that(Measurement((Sample(0.1, 0.25, 10), Sample(0.1, 0.75, 30))).average(), close_to(0.5, 1e-9), "measurement did not average request time")

    def test_finds_median_request_time(self):
        assert_that(Measurement((Sample(0.1, 0.9, 1), Sample(0.1, 0.2, 1), Sample(0.1, 0.4, 1))).median(), close_to(0.4, 1e-9), "measurement did not find median request time")

    def test_finds_fastest_request_time(self):
        assert_that(Measurement((Sample(0.1, 0.9, 1), Sample(0.1, 0.2, 1), Sample(0.1, 0.4, 1))).fastest(), close_to(0.2, 1e-9), "measurement did not find fastest request time")

    def test_finds_slowest_request_time(self):
        assert_that(Measurement((Sample(0.1, 0.9, 1), Sample(0.1, 0.2, 1), Sample(0.1, 0.4, 1))).slowest(), close_to(0.9, 1e-9), "measurement did not find slowest request time")

    def test_averages_latency(self):
        assert_that(Measurement((Sample(0.02, 0.5, 1), Sample(0.06, 0.5, 1))).latency(), close_to(0.04, 1e-9), "measurement did not average latency")

    def test_computes_speed_in_megabits_per_second(self):
        assert_that(Measurement((Sample(0.1, 0.5, 1_250_000), Sample(0.1, 1.5, 3_750_000))).speed(), close_to(20.0, 1e-9), "measurement did not compute speed in megabits per second")

    def test_computes_throughput_without_latency(self):
        assert_that(Measurement((Sample(0.5, 1.5, 1_250_000), Sample(0.5, 1.5, 1_250_000))).throughput(), close_to(10.0, 1e-9), "measurement did not compute throughput without latency")

    def test_rejects_empty_samples(self):
        with self.assertRaises(ValueError, msg="measurement did not reject empty samples"):
            Measurement(())


class ReportTest(unittest.TestCase):
    def test_prints_speed_in_megabits_per_second(self):
        assert_that(Report(Measurement((Sample(0.5, 1.0, 2_500_000),))).text(), contains_string("Download speed: 20.00 Mbit/s"), "report did not print speed in megabits per second")

    def test_prints_median_request_time(self):
        assert_that(Report(Measurement((Sample(0.1, 0.3, 1), Sample(0.1, 0.5, 1), Sample(0.1, 0.9, 1)))).text(), contains_string("median 500.0 ms"), "report did not print median request time")

    def test_exports_speed_as_json(self):
        assert_that(json.loads(Report(Measurement((Sample(0.5, 1.0, 2_500_000),))).json())["speed_mbit_s"], close_to(20.0, 1e-9), "report did not export speed as json")

    def test_exports_every_request_as_json(self):
        count = random.randint(2, 9)
        assert_that(len(json.loads(Report(Measurement(tuple(Sample(0.1, 0.2, 5) for _ in range(count)))).json())["requests"]), equal_to(count), "report did not export every request as json")


class ConsoleTest(unittest.TestCase):
    def test_prints_one_line_per_completed_request(self):
        stream = io.StringIO()
        console = Console(stream)
        console.complete("Request 1/2", Sample(0.1, 0.2, 1000))
        console.complete("Request 2/2", Sample(0.1, 0.2, 1000))
        assert_that(stream.getvalue().count("\n"), equal_to(2), "console did not print one line per completed request")

    def test_hides_live_counter_outside_terminal(self):
        stream = io.StringIO()
        Console(stream).advance(random.randint(1, 10_000_000), None)
        assert_that(stream.getvalue(), equal_to(""), "console did not hide live counter outside terminal")

    def test_shows_live_counter_in_terminal(self):
        stream = Terminal()
        Console(stream).advance(3_500_000, 7_000_000)
        assert_that(stream.getvalue(), contains_string("3.50 / 7.00 MB"), "console did not show live counter in terminal")

    def test_keeps_report_free_of_carriage_returns_outside_terminal(self):
        stream = io.StringIO()
        Console(stream).complete("Request 1/1", Sample(0.1, 0.2, 1000))
        assert_that(stream.getvalue(), is_not(contains_string("\r")), "console did not keep output free of carriage returns outside terminal")


if __name__ == "__main__":
    unittest.main()
