from __future__ import annotations

from types import SimpleNamespace

from app.api_handlers import stocks


def _ctx_with_execute(execute_fn):
	conf = {
		"ALPACA_EQUITIES_TABLE": "public.equities",
		"ALPACA_STOCK_BARS_TABLE": "public.stock_bars",
	}
	return SimpleNamespace(
		auth_token_name="session",
		interface=SimpleNamespace(execute_query=lambda *_args, **_kwargs: []),
		alpaca_db_client=SimpleNamespace(execute_query=execute_fn),
		fcr=SimpleNamespace(find=lambda _: conf),
	)


def test_stocks_search_empty_query(app_factory):
	ctx = _ctx_with_execute(lambda *_args, **_kwargs: [])
	app = app_factory(stocks.register, ctx)
	client = app.test_client()

	resp = client.get("/api/stocks/search?q=")
	assert resp.status_code == 200
	body = resp.get_json()
	assert body["ok"] is True
	assert body["count"] == 0
	assert body["results"] == []


def test_stocks_search_returns_results(app_factory):
	def _execute(query, params=None):
		assert "FROM \"public\".\"equities\"" in query
		assert params[1] == "5Min"
		return [{
			"symbol": "AAPL",
			"name": "Apple Inc.",
			"exchange": "NASDAQ",
			"status": "active",
			"tradable": True,
		}]

	ctx = _ctx_with_execute(_execute)
	app = app_factory(stocks.register, ctx)
	client = app.test_client()

	resp = client.get("/api/stocks/search?q=aap")
	assert resp.status_code == 200
	body = resp.get_json()
	assert body["ok"] is True
	assert body["count"] == 1
	assert body["results"][0]["symbol"] == "AAPL"


def test_stock_prices_success(app_factory):
	def _execute(query, params=None):
		assert "FROM \"public\".\"stock_bars\"" in query
		return [
			{
				"ts": "2026-01-01T00:00:00Z",
				"open": 100.0,
				"high": 110.0,
				"low": 99.0,
				"close": 105.0,
				"volume": 1000,
				"trade_count": 10,
				"vwap": 104.0,
			},
			{
				"ts": "2026-01-02T00:00:00Z",
				"open": 105.0,
				"high": 112.0,
				"low": 104.0,
				"close": 110.0,
				"volume": 1400,
				"trade_count": 14,
				"vwap": 109.0,
			},
		]

	ctx = _ctx_with_execute(_execute)
	app = app_factory(stocks.register, ctx)
	client = app.test_client()

	resp = client.get("/api/stocks/AAPL/prices?range=1M&timeframe=1Day")
	assert resp.status_code == 200
	body = resp.get_json()
	assert body["ok"] is True
	assert body["symbol"] == "AAPL"
	assert body["count"] == 2
	assert body["summary"]["latest_close"] == 110.0
	assert body["summary"]["change"] == 5.0


def test_stock_prices_reject_invalid_window(app_factory):
	ctx = _ctx_with_execute(lambda *_args, **_kwargs: [])
	app = app_factory(stocks.register, ctx)
	client = app.test_client()

	resp = client.get("/api/stocks/AAPL/prices?start=2026-02-01&end=2026-01-01")
	assert resp.status_code == 400
	body = resp.get_json()
	assert body["ok"] is False


class _MissingTableError(Exception):
	pgcode = "42P01"


def test_stocks_search_missing_table_is_handled(app_factory):
	def _execute(_query, _params=None):
		raise _MissingTableError("relation does not exist")

	ctx = _ctx_with_execute(_execute)
	app = app_factory(stocks.register, ctx)
	client = app.test_client()

	resp = client.get("/api/stocks/search?q=nvidia")
	assert resp.status_code == 200
	body = resp.get_json()
	assert body["ok"] is True
	assert body["count"] == 0
	assert "Stock tables are not available" in body["message"]


def test_stock_prices_missing_table_is_handled(app_factory):
	def _execute(_query, _params=None):
		raise _MissingTableError("relation does not exist")

	ctx = _ctx_with_execute(_execute)
	app = app_factory(stocks.register, ctx)
	client = app.test_client()

	resp = client.get("/api/stocks/AAPL/prices")
	assert resp.status_code == 503
	body = resp.get_json()
	assert body["ok"] is False
	assert body["count"] == 0
	assert "Stock price tables are not available" in body["message"]


def test_stock_prices_defaults_to_5min_and_caps_to_rolling_window(app_factory):
	def _execute(_query, params=None):
		assert params[0] == "AAPL"
		assert params[1] == "5Min"
		assert str(params[2]) == "2026-02-01 00:00:00+00:00"
		assert str(params[3]) == "2026-02-08 00:00:00+00:00"
		return []

	ctx = _ctx_with_execute(_execute)
	app = app_factory(stocks.register, ctx)
	client = app.test_client()

	resp = client.get("/api/stocks/AAPL/prices?range=MAX&end=2026-02-08T00:00:00Z")
	assert resp.status_code == 200
	body = resp.get_json()
	assert body["ok"] is True
	assert body["timeframe"] == "5Min"
