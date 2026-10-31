"""API call counts, reported to Google Cloud Monitoring as a custom metric.

Every completed API call is counted by the name of the function that handled
it and the HTTP response code it returned, and the counts are published as
[METRIC_TYPE] (labels: `function_name`, `response_code`). The counting itself
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

import logging
import threading
import time
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

METRIC_TYPE = "custom.googleapis.com/game_platform/api_calls"
_FLUSH_INTERVAL_SECONDS = 60.0
# One CreateTimeSeries request accepts at most 200 time series.
_MAX_SERIES_PER_WRITE = 200


class ApiCallCounter:
    """Thread-safe cumulative counts of API calls by (function, status)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter[tuple[str, int]] = Counter()
        # All series of a cumulative metric share this start time.
        self.started_at = time.time()

    def record(self, function_name: str, status: int) -> None:
        with self._lock:
            self._counts[(function_name, status)] += 1

    def snapshot(self) -> dict[tuple[str, int], int]:
        with self._lock:
            return dict(self._counts)


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

        try:
            await self._app(scope, receive, recording_send)
        finally:
            name = function_name_for_scope(scope)
            if name is not None:
                self._counter.record(name, status or 500)


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
                "API call metrics: reporting %s to %s every %.0fs",
                METRIC_TYPE,
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

        descriptor = metric_pb2.MetricDescriptor(
            type=METRIC_TYPE,
            labels=[
                label_pb2.LabelDescriptor(
                    key="function_name",
                    value_type=label_pb2.LabelDescriptor.ValueType.STRING,
                    description="Name of the API function that handled the call",
                ),
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
        )
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
        from google.api import metric_pb2, monitored_resource_pb2
        from google.cloud import monitoring_v3
        from google.protobuf import timestamp_pb2

        counts = self.counter.snapshot()
        if not counts:
            return
        start = timestamp_pb2.Timestamp()
        start.FromSeconds(int(self.counter.started_at))
        end = timestamp_pb2.Timestamp()
        end.GetCurrentTime()
        series = []
        for (function_name, status), value in sorted(counts.items()):
            point = monitoring_v3.Point(
                interval=monitoring_v3.TimeInterval(start_time=start, end_time=end),
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
                    resource=monitored_resource_pb2.MonitoredResource(
                        type="global",
                        labels={"project_id": self._project_name.rsplit("/", 1)[-1]},
                    ),
                    metric_kind=metric_pb2.MetricDescriptor.MetricKind.CUMULATIVE,
                    value_type=metric_pb2.MetricDescriptor.ValueType.INT64,
                    points=[point],
                )
            )
        for offset in range(0, len(series), _MAX_SERIES_PER_WRITE):
            self._client.create_time_series(
                name=self._project_name,
                time_series=series[offset : offset + _MAX_SERIES_PER_WRITE],
            )
