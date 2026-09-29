"""Tests for the Prometheus /metrics endpoint."""


class TestMetricsEndpoint:
    async def test_metrics_returns_prometheus_format(self, auth_client):
        r = await auth_client.get("/metrics")
        assert r.status_code == 200
        assert "http_requests_total" in r.text
