import argparse
import http.client
import json
import statistics
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Protocol, TextIO, final


@final
@dataclass(frozen=True)
class Sample:
    """
    Timing of one download: seconds until the response headers arrived,
    seconds until the last byte of the body arrived, and body size in bytes.
    """

    latency: float
    seconds: float
    size: int


class Progress(Protocol):
    """
    Observer of a running benchmark, told about bytes as they arrive
    and about every request once it has finished.
    """

    def advance(self, done: int, total: int | None) -> None: ...

    def complete(self, label: str, sample: Sample) -> None: ...


@final
class Console:
    """
    Progress written to a text stream: a live byte counter while a body
    downloads, shown only on an interactive terminal, and one summary line
    per finished request.
    """

    def __init__(self, stream: TextIO):
        self._stream = stream

    def advance(self, done: int, total: int | None) -> None:
        if self._stream.isatty():
            self._stream.write(f"{self._erase()}{done / 1_000_000:.2f} / {total / 1_000_000:.2f} MB" if total else f"{self._erase()}{done / 1_000_000:.2f} MB")
            self._stream.flush()

    def complete(self, label: str, sample: Sample) -> None:
        self._stream.write(
            f"{self._erase()}{label}: {sample.seconds * 1000:.1f} ms"
            f" (latency {sample.latency * 1000:.1f} ms), {sample.size / 1_000_000:.2f} MB\n"
        )
        self._stream.flush()

    def _erase(self) -> str:
        return "\r\033[K" if self._stream.isatty() else ""


@final
class Download:
    """
    One complete HTTP GET of a resource, timed from sending the request
    until the headers arrive and until the last byte of the body arrives.
    """

    def __init__(self, url: str, timeout: float):
        self._url = url
        self._timeout = timeout

    def sample(self, progress: Progress) -> Sample:
        start = time.perf_counter()
        size = 0
        with urllib.request.urlopen(self._url, timeout=self._timeout) as response:
            latency = time.perf_counter() - start
            total = response.length
            while chunk := response.read(65536):
                size += len(chunk)
                progress.advance(size, total)
        return Sample(latency, time.perf_counter() - start, size)


@final
class Measurement:
    """
    Outcome of several downloads, summarised as request time statistics,
    average latency, total volume and speed.
    """

    def __init__(self, samples: tuple[Sample, ...]):
        if not samples:
            raise ValueError("Measurement needs at least one sample")
        self._samples = samples

    def samples(self) -> tuple[Sample, ...]:
        return self._samples

    def average(self) -> float:
        return statistics.fmean(sample.seconds for sample in self._samples)

    def median(self) -> float:
        return statistics.median(sample.seconds for sample in self._samples)

    def fastest(self) -> float:
        return min(sample.seconds for sample in self._samples)

    def slowest(self) -> float:
        return max(sample.seconds for sample in self._samples)

    def latency(self) -> float:
        return statistics.fmean(sample.latency for sample in self._samples)

    def volume(self) -> int:
        return sum(sample.size for sample in self._samples)

    def speed(self) -> float:
        return self.volume() * 8 / sum(sample.seconds for sample in self._samples) / 1_000_000

    def throughput(self) -> float:
        return self.volume() * 8 / sum(sample.seconds - sample.latency for sample in self._samples) / 1_000_000


@final
class Benchmark:
    """
    A series of downloads of the same resource, run strictly one after
    another, optionally preceded by warm-up downloads that are not counted.
    """

    def __init__(self, download: Download, count: int, warmup: int, progress: Progress):
        if count < 1:
            raise ValueError(f"Request count must be positive, got {count}")
        if warmup < 0:
            raise ValueError(f"Warm-up count cannot be negative, got {warmup}")
        self._download = download
        self._count = count
        self._warmup = warmup
        self._progress = progress

    def measurement(self) -> Measurement:
        for number in range(1, self._warmup + 1):
            self._sample(f"Warm-up {number}/{self._warmup}")
        return Measurement(tuple(self._sample(f"Request {number}/{self._count}") for number in range(1, self._count + 1)))

    def _sample(self, label: str) -> Sample:
        sample = self._download.sample(self._progress)
        self._progress.complete(label, sample)
        return sample


@final
class Report:
    """
    Summary of a measurement, either as human-readable text or as JSON,
    using milliseconds for time, megabytes for volume and megabits per
    second for speed.
    """

    def __init__(self, measurement: Measurement):
        self._measurement = measurement

    def text(self) -> str:
        return "\n".join(
            (
                f"Requests: {len(self._measurement.samples())}",
                f"Request time: average {self._measurement.average() * 1000:.1f} ms, median {self._measurement.median() * 1000:.1f} ms,"
                f" min {self._measurement.fastest() * 1000:.1f} ms, max {self._measurement.slowest() * 1000:.1f} ms",
                f"Latency (time to first byte): average {self._measurement.latency() * 1000:.1f} ms",
                f"Total downloaded: {self._measurement.volume() / 1_000_000:.2f} MB",
                f"Download speed: {self._measurement.speed():.2f} Mbit/s",
                f"Download speed without latency: {self._measurement.throughput():.2f} Mbit/s",
            )
        )

    def json(self) -> str:
        return json.dumps(
            {
                "requests": [
                    {"latency_ms": sample.latency * 1000, "time_ms": sample.seconds * 1000, "bytes": sample.size}
                    for sample in self._measurement.samples()
                ],
                "average_ms": self._measurement.average() * 1000,
                "median_ms": self._measurement.median() * 1000,
                "min_ms": self._measurement.fastest() * 1000,
                "max_ms": self._measurement.slowest() * 1000,
                "latency_ms": self._measurement.latency() * 1000,
                "total_bytes": self._measurement.volume(),
                "speed_mbit_s": self._measurement.speed(),
                "speed_without_latency_mbit_s": self._measurement.throughput(),
            },
            indent=2,
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Measure download speed by fetching a URL several times in a row")
    parser.add_argument("url", help="address to download, for example a large image")
    parser.add_argument("-n", "--count", type=int, default=10, help="number of sequential requests (default: 10)")
    parser.add_argument("-w", "--warmup", type=int, default=0, help="extra requests made first and left out of the results (default: 0)")
    parser.add_argument("-t", "--timeout", type=float, default=30.0, help="timeout per request in seconds (default: 30)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)
    try:
        report = Report(Benchmark(Download(args.url, args.timeout), args.count, args.warmup, Console(sys.stderr)).measurement())
    except (OSError, http.client.HTTPException, ValueError) as error:
        print(f"Cannot measure speed of {args.url}: {error}", file=sys.stderr)
        return 1
    print(report.json() if args.json else report.text())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
