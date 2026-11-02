"""API call metrics (metrics.py): counting by handler function + response code,
and the Cloud Monitoring flush, with a fake client — no network, no credentials.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from game_platform import metrics
from game_platform.metrics import (
    LATENCY_BOUNDS_SECONDS,
    LATENCY_METRIC_TYPE,
    METRIC_TYPE,
    ApiCallCounter,
    ApiCallMetricsMiddleware,
    ApiMetricsReporter,
    function_name_for_scope,
)


def test_counter_aggregates_by_function_and_status() -> None:
    counter = ApiCallCounter()
    counter.record("get_user", 200)
    counter.record("get_user", 200)
    counter.record("get_user", 404)
    counter.record("create_match", 201)
    assert counter.snapshot() == {
        ("get_user", 200): 2,
        ("get_user", 404): 1,
        ("create_match", 201): 1,
    }


def test_counter_buckets_latencies_by_function() -> None:
    counter = ApiCallCounter()
    counter.record("get_user", 200, duration_seconds=0.03)
    counter.record("get_user", 200, duration_seconds=0.7)
    counter.record("get_user", 404)  # no duration: counted, not timed
    counter.record("create_match", 201, duration_seconds=2.0)
    latency = counter.latency_snapshot()
    sample = latency["get_user"]
    assert sample.count == 2
    # 0.03 lands in the 0.025-0.05 bucket (index 3), 0.7 in 0.5-1.0 (index 7).
    assert sample.bucket_counts == (0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)
    assert sample.sum_seconds == 0.73
    assert sample.mean == 0.365
    assert latency["create_match"].bucket_counts[8] == 1  # the 1.0-2.5 bucket


def test_function_name_for_scope_reads_the_matched_endpoint() -> None:
    def handler() -> None: ...

    handler.__module__ = "game_platform.api"
    assert function_name_for_scope({"endpoint": handler}) == "handler"
    handler.__module__ = "starlette.staticfiles"
    assert function_name_for_scope({"endpoint": handler}) is None
    assert function_name_for_scope({}) is None


def _counting_client(counter: ApiCallCounter) -> TestClient:
    app = FastAPI()

    @app.get("/things")
    def list_things() -> dict:
        return {"ok": True}

    @app.get("/broken")
    def broken() -> dict:
        raise RuntimeError("boom")

    app.add_middleware(ApiCallMetricsMiddleware, counter=counter)
    return TestClient(app, raise_server_exceptions=False)


def test_middleware_counts_api_calls_with_their_status() -> None:
    counter = ApiCallCounter()
    client = _counting_client(counter)
    assert client.get("/things").status_code == 200
    assert client.get("/things").status_code == 200
    assert client.get("/broken").status_code == 500
    # Unmatched paths have no handling function: not API calls, not counted.
    assert client.get("/nope").status_code == 404
    assert counter.snapshot() == {("list_things", 200): 2, ("broken", 500): 1}
    latency = counter.latency_snapshot()
    assert latency["list_things"].count == 2
    assert latency["broken"].count == 1


def test_middleware_counts_the_platforms_own_handlers() -> None:
    from game_platform.api import create_app

    counter = ApiCallCounter()
    app = create_app()
    app.add_middleware(ApiCallMetricsMiddleware, counter=counter)
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert counter.snapshot() == {("health", 200): 1}


class _FakeMonitoringClient:
    def __init__(self) -> None:
        self.writes: list[list] = []

    def create_time_series(self, *, name: str, time_series: list) -> None:
        self.writes.append(list(time_series))


def _reporter_with(counter: ApiCallCounter) -> tuple[ApiMetricsReporter, _FakeMonitoringClient]:
    reporter = ApiMetricsReporter(counter=counter)
    client = _FakeMonitoringClient()
    reporter._client = client
    reporter._project_name = "projects/test-project"
    return reporter, client


def test_flush_writes_cumulative_series_with_function_and_code_labels() -> None:
    counter = ApiCallCounter()
    counter.record("get_user", 200)
    counter.record("get_user", 200)
    counter.record("create_match", 404)
    reporter, client = _reporter_with(counter)
    reporter.flush()
    (write,) = client.writes
    by_labels = {
        (s.metric.labels["function_name"], s.metric.labels["response_code"]): s
        for s in write
    }
    assert set(by_labels) == {("get_user", "200"), ("create_match", "404")}
    series = by_labels[("get_user", "200")]
    assert series.metric.type == METRIC_TYPE
    assert series.points[0].value.int64_value == 2
    assert int(series.points[0].interval.start_time.timestamp()) == int(counter.started_at)
    assert series.resource.type == "global"
    assert series.resource.labels["project_id"] == "test-project"


def test_flush_writes_latency_distribution_series() -> None:
    counter = ApiCallCounter()
    counter.record("get_user", 200, duration_seconds=0.03)
    counter.record("get_user", 200, duration_seconds=0.7)
    counter.record("create_match", 404)  # counted but never timed: no series
    reporter, client = _reporter_with(counter)
    reporter.flush()
    counts_write, latency_write = client.writes
    assert {s.metric.type for s in counts_write} == {METRIC_TYPE}
    (series,) = latency_write
    assert series.metric.type == LATENCY_METRIC_TYPE
    assert series.metric.labels["function_name"] == "get_user"
    value = series.points[0].value.distribution_value
    assert value.count == 2
    assert value.mean == 0.365
    assert list(value.bucket_counts) == [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
    assert list(value.bucket_options.explicit_buckets.bounds) == list(
        LATENCY_BOUNDS_SECONDS
    )
    assert int(series.points[0].interval.start_time.timestamp()) == int(counter.started_at)


def test_flush_chunks_writes_at_200_series() -> None:
    counter = ApiCallCounter()
    for index in range(250):
        counter.record(f"handler_{index}", 200)
    reporter, client = _reporter_with(counter)
    reporter.flush()
    assert [len(write) for write in client.writes] == [200, 50]


def test_flush_with_no_calls_writes_nothing() -> None:
    reporter, client = _reporter_with(ApiCallCounter())
    reporter.flush()
    assert client.writes == []


def test_flush_never_raises() -> None:
    reporter = ApiMetricsReporter()
    reporter.counter.record("get_user", 200)
    reporter._client = None  # never connected
    reporter._project_name = "projects/test-project"
    reporter.flush()  # must swallow the failure


def test_connect_failure_disables_reporting_without_raising(monkeypatch) -> None:
    import google.auth

    def no_credentials():
        raise RuntimeError("no credentials here")

    monkeypatch.setattr(google.auth, "default", no_credentials)
    reporter = ApiMetricsReporter()
    assert reporter._connect() is False
