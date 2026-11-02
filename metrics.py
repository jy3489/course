"""API call counts, reported to Google Cloud Monitoring as a custom metric.

Every completed API call is counted by the name of the function that handled
it and the HTTP response code it returned, and the counts are published as
[METRIC_TYPE] (labels: `function_name`, `response_code`). The call's duration
is published alongside as [LATENCY_METRIC_TYPE], a distribution per function
whose buckets bracket one second, so Metrics Explorer can show the latency
percentiles the "no call over 1s" goal is judged by. The counting itself
is one in-memory increment per request, on the request path; a background
thread aggregates and writes the totals once a minute, so reporting never
slows a request down, never touches Firestore, and survives the Monitoring
API being briefly unreachable (counts are kept and retried with the next
flush). Cumulative semantics: each series starts when the process starts and
only grows, which is the shape Cloud Monitoring expects for a counter.

Off unless `monitoring.cloud_metrics_enabled` is set in the config: writing
custom metrics needs the `google-cloud-monitoring` package (in
requirements.txt) and a service account allowed to write metrics (on the
Google Cloud VM, the VM's own account). Any failure to set that up disables
reporting with one log line; the server itself is unaffected.
"""

import bisect
import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

METRIC_TYPE = "custom.googleapis.com/game_platform/api_calls"
LATENCY_METRIC_TYPE = "custom.googleapis.com/game_platform/api_call_latency"
# Bucket bounds (seconds) for the latency distribution: fine-grained where
# calls normally land, with 1.0 as an explicit edge so the share of calls
# over the one-second goal is readable straight from the distribution.
LATENCY_BOUNDS_SECONDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
_FLUSH_INTERVAL_SECONDS = 60.0
# One CreateTimeSeries request accepts at most 200 time series.
_MAX_SERIES_PER_WRITE = 200


@dataclass(frozen=True)
class LatencyDistribution:
    """One function's cumulative call durations, ready to publish."""

    bucket_counts: tuple[int, ...]
    count: int
    sum_seconds: float
    sum_squared_seconds: float

    @property
    def mean(self) -> float:
        return self.sum_seconds / self.count if self.count else 0.0

    @property
    def sum_of_squared_deviation(self) -> float:
        # sumsq - n*mean^2, clamped: float error can make it a hair negative.
        return max(0.0, self.sum_squared_seconds - self.count * self.mean**2)


class _LatencyAccumulator:
    def __init__(self) -> None:
        self.bucket_counts = [0] * (len(LATENCY_BOUNDS_SECONDS) + 1)
        self.count = 0
        self.sum_seconds = 0.0
        self.sum_squared_seconds = 0.0

    def add(self, duration_seconds: float) -> None:
        self.bucket_counts[bisect.bisect_right(LATENCY_BOUNDS_SECONDS, duration_seconds)] += 1
        self.count += 1
        self.sum_seconds += duration_seconds
        self.sum_squared_seconds += duration_seconds * duration_seconds

    def snapshot(self) -> LatencyDistribution:
        return LatencyDistribution(
            bucket_counts=tuple(self.bucket_counts),
            count=self.count,
            sum_seconds=self.sum_seconds,
            sum_squared_seconds=self.sum_squared_seconds,
        )


class ApiCallCounter:
    """Thread-safe cumulative counts of API calls by (function, status),
    plus a cumulative latency distribution per function."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter[tuple[str, int]] = Counter()
        self._latency: dict[str, _LatencyAccumulator] = {}
        # All series of a cumulative metric share this start time.
        self.started_at = time.time()

    def record(
        self, function_name: str, status: int, *, duration_seconds: float | None = None
    ) -> None:
        with self._lock:
            self._counts[(function_name, status)] += 1
            if duration_seconds is not None:
                self._latency.setdefault(function_name, _LatencyAccumulator()).add(
                    duration_seconds
                )

    def snapshot(self) -> dict[tuple[str, int], int]:
        with self._lock:
            return dict(self._counts)

    def latency_snapshot(self) -> dict[str, LatencyDistribution]:
        with self._lock:
            return {name: acc.snapshot() for name, acc in self._latency.items()}


def function_name_for_scope(scope: dict[str, Any]) -> str | None:
    """The name of the platform function that handled a request, or None when
    the request was not an API call (static files, unmatched paths...).

    Starlette's router records the matched endpoint in the shared scope, so
    after the inner app has run, the handler is sitting in `scope`. Only the
    platform's own modules count: they are the API whose calls we report.
    """
    endpoint = scope.get("endpoint")
    if endpoint is None:
        return None
    if not getattr(endpoint, "__module__", "").startswith("game_platform."):
        return None
    return getattr(endpoint, "__name__", None)


class ApiCallMetricsMiddleware:
    """Pure ASGI middleware: counts each completed API call into [counter]."""

    def __init__(self, app: Any, *, counter: ApiCallCounter) -> None:
        self._app = app
        self._counter = counter

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        status: int | None = None

        async def recording_send(message: dict[str, Any]) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = message.get("status")
            await send(message)

        started = time.perf_counter()
        try:
            await self._app(scope, receive, recording_send)
        finally:
            name = function_name_for_scope(scope)
            if name is not None:
                self._counter.record(
                    name,
                    status or 500,
                    duration_seconds=time.perf_counter() - started,
                )


class ApiMetricsReporter:
    """Owns the counter and the background thread that publishes it.

    The Monitoring client is created inside the worker thread, so building a
    reporter (or importing this module) never fails: no credentials or a
    missing permission just means the thread logs once and reporting stays
    off, while the counter keeps counting harmlessly in memory.
    """

    def __init__(
        self,
        *,
        counter: ApiCallCounter | None = None,
        interval_seconds: float = _FLUSH_INTERVAL_SECONDS,
    ) -> None:
        self.counter = counter if counter is not None else ApiCallCounter()
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: Any = None
        self._project_name: str | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="api-metrics-reporter", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        if not self._connect():
            return
        while not self._stop.wait(self._interval_seconds):
            self.flush()

    def _connect(self) -> bool:
        try:
            import google.auth
            from google.cloud import monitoring_v3

            # The credentials' default project: on the VM, the VM's project.
            _credentials, project = google.auth.default()
            if not project:
                raise RuntimeError("no Google Cloud project from the credentials")
            self._client = monitoring_v3.MetricServiceClient()
            self._project_name = f"projects/{project}"
            self._ensure_descriptor()
            logger.info(
                "API call metrics: reporting %s and %s to %s every %.0fs",
                METRIC_TYPE,
                LATENCY_METRIC_TYPE,
                self._project_name,
                self._interval_seconds,
            )
            return True
        except Exception:
            logger.exception(
                "API call metrics are OFF: could not set up Cloud Monitoring "
                "reporting (the server is unaffected)"
            )
            return False

    def _ensure_descriptor(self) -> None:
        from google.api import label_pb2, metric_pb2
        from google.cloud import monitoring_v3

        function_label = label_pb2.LabelDescriptor(
            key="function_name",
            value_type=label_pb2.LabelDescriptor.ValueType.STRING,
            description="Name of the API function that handled the call",
        )
        descriptors = [
            metric_pb2.MetricDescriptor(
                type=METRIC_TYPE,
                labels=[
                    function_label,
                    label_pb2.LabelDescriptor(
                        key="response_code",
                        value_type=label_pb2.LabelDescriptor.ValueType.STRING,
                        description="HTTP response code returned by the call",
                    ),
                ],
                metric_kind=metric_pb2.MetricDescriptor.MetricKind.CUMULATIVE,
                value_type=metric_pb2.MetricDescriptor.ValueType.INT64,
                unit="1",
                description="API calls, by handling function and HTTP response code",
                display_name="Game platform API calls",
            ),
            metric_pb2.MetricDescriptor(
                type=LATENCY_METRIC_TYPE,
                labels=[function_label],
                metric_kind=metric_pb2.MetricDescriptor.MetricKind.CUMULATIVE,
                value_type=metric_pb2.MetricDescriptor.ValueType.DISTRIBUTION,
                unit="s",
                description=(
                    "API call durations in seconds, by handling function; "
                    "bucket bounds bracket one second"
                ),
                display_name="Game platform API call latency",
            ),
        ]
        for descriptor in descriptors:
            try:
                self._client.create_metric_descriptor(
                    name=self._project_name, metric_descriptor=descriptor
                )
            except Exception as error:
                # AlreadyExists is the expected steady state; anything else is
                # worth one line but must not stop the writes from being tried.
                if error.__class__.__name__ != "AlreadyExists":
                    logger.warning("could not create metric descriptor: %s", error)

    def flush(self) -> None:
        """Writes the current cumulative counts. Never raises."""
        try:
            self._flush()
        except Exception:
            logger.exception("API call metrics flush failed; counts are kept")

    def _flush(self) -> None:
        from google.api import distribution_pb2, metric_pb2, monitored_resource_pb2
        from google.cloud import monitoring_v3
        from google.protobuf import timestamp_pb2

        counts = self.counter.snapshot()
        if not counts:
            return
        start = timestamp_pb2.Timestamp()
        start.FromSeconds(int(self.counter.started_at))
        end = timestamp_pb2.Timestamp()
        end.GetCurrentTime()
        resource = monitored_resource_pb2.MonitoredResource(
            type="global",
            labels={"project_id": self._project_name.rsplit("/", 1)[-1]},
        )
        interval = monitoring_v3.TimeInterval(start_time=start, end_time=end)

        series = []
        for (function_name, status), value in sorted(counts.items()):
            point = monitoring_v3.Point(
                interval=interval,
                value=monitoring_v3.TypedValue(int64_value=value),
            )
            series.append(
                monitoring_v3.TimeSeries(
                    metric=metric_pb2.Metric(
                        type=METRIC_TYPE,
                        labels={
                            "function_name": function_name,
                            "response_code": str(status),
                        },
                    ),
                    resource=resource,
                    metric_kind=metric_pb2.MetricDescriptor.MetricKind.CUMULATIVE,
                    value_type=metric_pb2.MetricDescriptor.ValueType.INT64,
                    points=[point],
                )
            )
        self._write(series)

        latency_series = []
        for function_name, sample in sorted(self.counter.latency_snapshot().items()):
            distribution = distribution_pb2.Distribution(
                count=sample.count,
                mean=sample.mean,
                sum_of_squared_deviation=sample.sum_of_squared_deviation,
                bucket_options=distribution_pb2.Distribution.BucketOptions(
                    explicit_buckets=distribution_pb2.Distribution.BucketOptions.Explicit(
                        bounds=list(LATENCY_BOUNDS_SECONDS)
                    )
                ),
                bucket_counts=list(sample.bucket_counts),
            )
            point = monitoring_v3.Point(
                interval=interval,
                value=monitoring_v3.TypedValue(distribution_value=distribution),
            )
            latency_series.append(
                monitoring_v3.TimeSeries(
                    metric=metric_pb2.Metric(
                        type=LATENCY_METRIC_TYPE,
                        labels={"function_name": function_name},
                    ),
                    resource=resource,
                    metric_kind=metric_pb2.MetricDescriptor.MetricKind.CUMULATIVE,
                    value_type=metric_pb2.MetricDescriptor.ValueType.DISTRIBUTION,
                    points=[point],
                )
            )
        self._write(latency_series)

    def _write(self, series: list) -> None:
        for offset in range(0, len(series), _MAX_SERIES_PER_WRITE):
            self._client.create_time_series(
                name=self._project_name,
                time_series=series[offset : offset + _MAX_SERIES_PER_WRITE],
            )
