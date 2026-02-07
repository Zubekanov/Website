from __future__ import annotations

import flask
import importlib
import sys
from types import ModuleType


def _webpage_builder_cls():
	stub_metrics = ModuleType("util.webpage_builder.metrics_builder")
	stub_metrics.METRICS_NAMES = {}
	sys.modules["util.webpage_builder.metrics_builder"] = stub_metrics

	sys.modules.pop("util.webpage_builder.parent_builder", None)
	mod = importlib.import_module("util.webpage_builder.parent_builder")
	return mod.WebPageBuilder


def test_metadata_defaults_are_generated_from_page_state():
	WebPageBuilder = _webpage_builder_cls()
	app = flask.Flask(__name__)
	with app.test_request_context("/docs/page?tab=overview", base_url="https://example.test"):
		builder = WebPageBuilder()
		builder._remove_default_footer()
		builder.config_values["title"] = "Docs"
		builder.config_values["body_html"] = "<h1>Docs</h1><p>This page explains the project configuration and usage details.</p>"

		html = builder.serve_html()

	assert '<title>Docs</title>' in html
	assert 'name="description" content="Docs This page explains the project configuration and usage details."' in html
	assert 'rel="canonical" href="https://example.test/docs/page?tab=overview"' in html
	assert 'property="og:title" content="Docs"' in html
	assert 'name="twitter:title" content="Docs"' in html


def test_metadata_explicit_overrides_take_precedence():
	WebPageBuilder = _webpage_builder_cls()
	app = flask.Flask(__name__)
	with app.test_request_context("/profile", base_url="https://example.test"):
		builder = WebPageBuilder()
		builder._remove_default_footer()
		builder.config_values.update(
			{
				"title": "Profile",
				"meta_description": "Custom description",
				"canonical_url": "https://cdn.example.test/custom-canonical",
				"og_title": "Custom OG",
				"twitter_title": "Custom Twitter",
			}
		)
		html = builder.serve_html()

	assert 'name="description" content="Custom description"' in html
	assert 'rel="canonical" href="https://cdn.example.test/custom-canonical"' in html
	assert 'property="og:title" content="Custom OG"' in html
	assert 'name="twitter:title" content="Custom Twitter"' in html


def test_metadata_defaults_render_without_request_context():
	WebPageBuilder = _webpage_builder_cls()
	app = flask.Flask(__name__)
	with app.app_context():
		builder = WebPageBuilder()
		builder._remove_default_footer()
		html = builder.serve_html()

	assert '<title>Joseph Wong</title>' in html
	assert 'name="description" content="Personal website hosting various projects and information."' in html
	assert 'rel="canonical" href=""' in html
