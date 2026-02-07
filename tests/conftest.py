from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import flask
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
	sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture
def app_factory():
	def _build(register_fn, ctx):
		app = flask.Flask(__name__)
		bp = flask.Blueprint("api", __name__)
		register_fn(bp, ctx)
		app.register_blueprint(bp)
		app.config["TESTING"] = True
		return app

	return _build


@pytest.fixture
def simple_ctx():
	return SimpleNamespace(
		auth_token_name="session",
		interface=SimpleNamespace(client=SimpleNamespace()),
		fcr=SimpleNamespace(find=lambda _: None),
		minecraft_status_cache={"data": None, "fetched_at_ts": None, "refreshing": False},
		minecraft_status_lock=threading.Lock(),
	)
