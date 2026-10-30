"""Small, explicit capacity milestones; never extrapolates them into a scale claim.

Use an emulator or an isolated named database. The write budget is mandatory.
Python allocation peaks measure application allocations, not VM RSS or Firestore
server memory. Latencies are local client measurements, not production p99.
"""

import argparse
import json
import os
import statistics
import time
import tracemalloc

from google.cloud import firestore

from game_platform.query_store import QueryStore
from game_platform.scaling_service import ScalablePlatform
from game_platform.scaling_tools import seed


def measure(store):
    tracemalloc.start()
    start = time.perf_counter()
    restarted = QueryStore(store.client, namespace=store.namespace)
    ScalablePlatform(store=restarted)
    startup = time.perf_counter() - start
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    timings = []
    items = 0
    for _ in range(20):
        start = time.perf_counter()
        page = restarted.page("matches", limit=50)
        timings.append((time.perf_counter() - start) * 1000)
        items = len(page.items)
    return {
        "counts": store.counts(),
        "startup_ms": round(startup * 1000, 2),
        "startup_python_peak_bytes": peak,
        "page_items": items,
        "page_query_limit": 51,
        "page_ms_median": round(statistics.median(timings), 2),
        "page_ms_p95": round(sorted(timings)[18], 2),
        "samples": 20,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("project", "database", "namespace", "run-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--matches", type=int, required=True)
    parser.add_argument("--max-writes", type=int, required=True)
    args = parser.parse_args()
    if args.database == "(default)" and not os.environ.get("FIRESTORE_EMULATOR_HOST"):
        parser.error("benchmark requires an isolated named database")
    store = QueryStore.initialize(
        firestore.Client(project=args.project, database=args.database),
        namespace=args.namespace,
    )
    seed(
        store,
        run_id=args.run_id,
        users=100,
        games=60,
        matches=args.matches,
        moves_per_match=2,
        max_writes=args.max_writes,
    )
    print(json.dumps(measure(store), sort_keys=True))
