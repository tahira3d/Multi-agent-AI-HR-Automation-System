"""Small in-process Prometheus counters for request-level operational visibility."""
from __future__ import annotations

from collections import Counter
from threading import Lock

_lock = Lock()
_requests: Counter[tuple[str, str, str]] = Counter()


def record_request(method: str, route: str, status: int) -> None:
    with _lock:
        _requests[(method, route, str(status))] += 1


def render_metrics() -> str:
    with _lock:
        rows = list(_requests.items())
    output = [
        "# HELP http_requests_total Number of HTTP requests handled by this process.",
        "# TYPE http_requests_total counter",
    ]
    for (method, route, status), count in sorted(rows):
        safe_route = route.replace("\\", "\\\\").replace('"', '\\"')
        output.append(f'http_requests_total{{method="{method}",route="{safe_route}",status="{status}"}} {count}')
    return "\n".join(output) + "\n"
