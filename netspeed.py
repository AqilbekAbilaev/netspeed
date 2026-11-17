import argparse
import sys
import time
import urllib.error
import urllib.request
from typing import final


@final
class Download:
    """
    One complete HTTP GET of a resource, timed from sending the request
    until the last byte of the body arrives.
    """

    def __init__(self, url: str, timeout: float):
        self._url = url
        self._timeout = timeout

    def sample(self) -> tuple[float, int]:
        start = time.perf_counter()
        size = 0
        with urllib.request.urlopen(self._url, timeout=self._timeout) as response:
            while chunk := response.read(65536):
                size += len(chunk)
        return time.perf_counter() - start, size


@final
class Measurement:
    """
    Outcome of several downloads: how long each took and how many bytes
    each delivered, summarised as average time, total volume and speed.
    """

    def __init__(self, samples: tuple[tuple[float, int], ...]):
        if not samples:
            raise ValueError("Measurement needs at least one sample")
        self._samples = samples

    def average(self) -> float:
        return sum(seconds for seconds, _ in self._samples) / len(self._samples)

    def volume(self) -> int:
        return sum(size for _, size in self._samples)

    def speed(self) -> float:
        return self.volume() * 8 / sum(seconds for seconds, _ in self._samples) / 1_000_000


@final
class Benchmark:
    """
    A series of downloads of the same resource, run strictly one after
    another so that each request waits for the previous one to finish.
    """

    def __init__(self, download: Download, count: int):
        if count < 1:
            raise ValueError(f"Request count must be positive, got {count}")
        self._download = download
        self._count = count

    def measurement(self) -> Measurement:
        return Measurement(tuple(self._download.sample() for _ in range(self._count)))


@final
class Report:
    """
    Human-readable summary of a measurement, using milliseconds for time,
    megabytes for volume and megabits per second for speed.
    """

    def __init__(self, measurement: Measurement):
        self._measurement = measurement

    def text(self) -> str:
        return "\n".join(
            (
                f"Average request time: {self._measurement.average() * 1000:.1f} ms",
                f"Total downloaded: {self._measurement.volume() / 1_000_000:.2f} MB",
                f"Download speed: {self._measurement.speed():.2f} Mbit/s",
            )
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Measure download speed by fetching a URL several times in a row")
    parser.add_argument("url", help="address to download, for example a large image")
    parser.add_argument("-n", "--count", type=int, default=10, help="number of sequential requests (default: 10)")
    parser.add_argument("-t", "--timeout", type=float, default=30.0, help="timeout per request in seconds (default: 30)")
    args = parser.parse_args(argv)
    try:
        print(Report(Benchmark(Download(args.url, args.timeout), args.count).measurement()).text())
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        print(f"Cannot measure speed of {args.url}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
