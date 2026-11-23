import json
import logging

from app.core.logging import JsonFormatter, correlation_id, redact
from app.core.observability import record_request, render_metrics


def test_json_logs_include_correlation_and_redact_private_values():
    token = correlation_id.set("request-test-1")
    try:
        record = logging.LogRecord(
            "test", logging.INFO, __file__, 1,
            "candidate=%s email=%s key=%s", ("Synthetic", "person@example.com", "gsk_12345678901234567890"),
            None,
        )
        result = json.loads(JsonFormatter().format(record))
    finally:
        correlation_id.reset(token)
    assert result["request_id"] == "request-test-1"
    assert "person@example.com" not in result["message"]
    assert "gsk_12345678901234567890" not in result["message"]
    assert "Synthetic" in result["message"]


def test_redaction_does_not_change_non_sensitive_messages():
    assert redact("evaluation completed") == "evaluation completed"


def test_metrics_use_route_templates_and_prometheus_format():
    record_request("GET", "/health/ready", 200)
    output = render_metrics()
    assert "# TYPE http_requests_total counter" in output
    assert 'method="GET",route="/health/ready",status="200"' in output
