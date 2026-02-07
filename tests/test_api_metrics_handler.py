from __future__ import annotations

from types import SimpleNamespace

from app.api_handlers import metrics


def test_api_metrics_rejects_count_and_since(app_factory, simple_ctx):
	app = app_factory(metrics.register, simple_ctx)
	client = app.test_client()

	resp = client.get("/api/metrics/cpu?count=10&since=2026-01-01T00:00:00+00:00")
	assert resp.status_code == 400
	assert "Do not specify both 'count' and 'since'" in resp.get_json()["error"]


def test_api_metrics_window_requires_bucket(app_factory, simple_ctx):
	app = app_factory(metrics.register, simple_ctx)
	client = app.test_client()

	resp = client.get("/api/metrics/cpu?window=120")
	assert resp.status_code == 400
	assert resp.get_json()["error"] == "bucket is required when window is specified."


def test_api_metrics_success_path(monkeypatch, app_factory, simple_ctx):
	monkeypatch.setattr(metrics, "_get_metrics", lambda metric, num_entries, format_ts: (["t1"], [1.0]))
	app = app_factory(metrics.register, simple_ctx)
	client = app.test_client()

	resp = client.get("/api/metrics/cpu")
	assert resp.status_code == 200
	body = resp.get_json()
	assert body["error"] is None
	assert body["timestamps"] == ["t1"]
	assert body["data"] == [1.0]


def test_api_metrics_bulk_requires_metrics(app_factory, simple_ctx):
	app = app_factory(metrics.register, simple_ctx)
	client = app.test_client()

	resp = client.get("/api/metrics/bulk")
	assert resp.status_code == 400
	assert resp.get_json()["error"] == "metrics parameter is required."


def test_api_metrics_bulk_non_window_success(monkeypatch, app_factory, simple_ctx):
	monkeypatch.setattr(metrics, "_get_metrics_bulk", lambda metrics_arg, num_entries, format_ts: (["t1"], {"cpu": [1]}))
	app = app_factory(metrics.register, simple_ctx)
	client = app.test_client()

	resp = client.get("/api/metrics/bulk?metrics=cpu")
	assert resp.status_code == 200
	body = resp.get_json()
	assert body["error"] is None
	assert body["data"] == {"cpu": [1]}


def test_api_metrics_names(monkeypatch, app_factory, simple_ctx):
	monkeypatch.setattr(metrics, "_metrics_names_and_units", lambda: (["cpu"], {"cpu": "%"}))
	app = app_factory(metrics.register, simple_ctx)
	client = app.test_client()

	resp = client.get("/api/metrics/names")
	assert resp.status_code == 200
	assert resp.get_json() == {"names": ["cpu"], "units": {"cpu": "%"}}


def test_api_metrics_metric_value_error(monkeypatch, app_factory, simple_ctx):
	def _raise(metric, num_entries, format_ts):
		raise ValueError("unknown metric")

	monkeypatch.setattr(metrics, "_get_metrics", _raise)
	app = app_factory(metrics.register, simple_ctx)
	client = app.test_client()

	resp = client.get("/api/metrics/notreal")
	assert resp.status_code == 400
	assert resp.get_json()["error"] == "unknown metric"


def test_api_metrics_bulk_window_success(monkeypatch, app_factory, simple_ctx):
	monkeypatch.setattr(metrics, "_get_metrics_bucketed", lambda metric, since_dt, bucket_seconds, format_ts: (["t1"], [1]))
	app = app_factory(metrics.register, simple_ctx)
	client = app.test_client()

	resp = client.get("/api/metrics/bulk?metrics=cpu,ram&window=120&bucket=10")
	assert resp.status_code == 200
	body = resp.get_json()
	assert body["error"] is None
	assert body["timestamps"] == ["t1"]
	assert body["data"] == {"cpu": [1], "ram": [1]}


def test_api_metrics_bulk_window_metric_failure(monkeypatch, app_factory, simple_ctx):
	def _bucket(metric, since_dt, bucket_seconds, format_ts):
		if metric == "ram":
			raise RuntimeError("boom")
		return ["t1"], [1]

	monkeypatch.setattr(metrics, "_get_metrics_bucketed", _bucket)
	app = app_factory(metrics.register, simple_ctx)
	client = app.test_client()

	resp = client.get("/api/metrics/bulk?metrics=cpu,ram&window=120&bucket=10")
	assert resp.status_code == 500
	assert "Failed to fetch metric 'ram'" in resp.get_json()["error"]


def test_api_metrics_update_success_and_empty(monkeypatch, app_factory, simple_ctx):
	app = app_factory(metrics.register, simple_ctx)
	client = app.test_client()

	monkeypatch.setattr(metrics, "_get_latest_metrics_entry", lambda num_entries=1: [{"cpu": 1}])
	ok_resp = client.get("/api/metrics/update")
	assert ok_resp.status_code == 200
	assert ok_resp.get_json()["data"] == {"cpu": 1}

	monkeypatch.setattr(metrics, "_get_latest_metrics_entry", lambda num_entries=1: [])
	err_resp = client.get("/api/metrics/update")
	assert err_resp.status_code == 500
	assert err_resp.get_json()["error"] == "No metrics data available."
