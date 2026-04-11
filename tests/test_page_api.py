from __future__ import annotations

import importlib
import flask
import sys
from types import ModuleType

from util.webpage_builder.page_api import (
	Form,
	FormAction,
	Page,
	PageContext,
	RawHtml,
	SubmitButton,
	TextField,
)


def _test_app() -> flask.Flask:
	app = flask.Flask(__name__)
	resources = flask.Blueprint("resources", __name__)

	@resources.route("/resume")
	def serve_resume():
		return "resume"

	app.register_blueprint(resources)
	return app


def _webpage_builder_module(monkeypatch):
	stub_psql = ModuleType("sql.psql_interface")

	class _PSQLInterface:
		def is_admin(self, user_id):
			_ = user_id
			return False

	stub_psql.PSQLInterface = _PSQLInterface
	monkeypatch.setitem(sys.modules, "sql.psql_interface", stub_psql)
	sys.modules.pop("util.webpage_builder.webpage_builder", None)
	return importlib.import_module("util.webpage_builder.webpage_builder")


def test_page_aggregates_assets_and_boot_data():
	app = _test_app()
	with app.test_request_context("/component", base_url="https://example.test"):
		ctx = PageContext.current()
		page = Page(
			title="Component Test",
			children=(
				RawHtml(
					"<section>One</section>",
					stylesheets=("/static/css/a.css", "/static/css/shared.css"),
					scripts=("/static/js/a.js", "/static/js/shared.js"),
					boot_data={"one": 1},
				),
				RawHtml(
					"<section>Two</section>",
					stylesheets=("/static/css/b.css", "/static/css/shared.css"),
					scripts=("/static/js/b.js", "/static/js/shared.js"),
					boot_data={"two": 2},
				),
			),
		)
		html = page.render(ctx)

	assert html.count("/static/css/shared.css") == 1
	assert html.count("/static/js/shared.js") == 1
	assert '"one":1' in html
	assert '"two":2' in html


def test_form_renders_root_submit_contract_and_assets():
	app = _test_app()
	with app.test_request_context("/form", base_url="https://example.test"):
		ctx = PageContext.current()
		action = FormAction(
			route="/submit",
			method="POST",
			success_redirect="/done",
			failure_redirect="/retry",
			refresh_on_success=True,
			refresh_on_failure=True,
		)
		form = Form(
			form_id="example-form",
			action=action,
			fields=(TextField(label="Name", name="name"),),
			submit_buttons=(SubmitButton(label="Send"),),
		)
		rendered = form.render(ctx)

	assert 'id="example-form"' in rendered.html
	assert 'data-form-submit-route="/submit"' in rendered.html
	assert 'data-form-submit-method="POST"' in rendered.html
	assert 'data-form-success-redirect="/done"' in rendered.html
	assert 'data-form-failure-redirect="/retry"' in rendered.html
	assert 'data-form-success-refresh="true"' in rendered.html
	assert 'data-form-failure-refresh="true"' in rendered.html
	assert "data-form-message" in rendered.html
	assert "/static/css/forms.css" in rendered.stylesheets
	assert "/static/js/form_submit.js" in rendered.scripts


def test_raw_html_passthrough_is_not_escaped():
	app = _test_app()
	with app.test_request_context("/raw", base_url="https://example.test"):
		rendered = RawHtml("<div><strong>raw</strong></div>").render(PageContext.current())
	assert rendered.html == "<div><strong>raw</strong></div>"


def test_login_page_keeps_button_compatibility_attrs(monkeypatch):
	webpage_builder = _webpage_builder_module(monkeypatch)
	app = _test_app()
	with app.test_request_context("/login", base_url="https://example.test"):
		html = webpage_builder.build_login_page(None)

	assert 'data-form-submit-route="/login"' in html
	assert 'data-submit-route="/login"' in html
	assert "Remember me" in html


def test_integration_remove_page_reads_query_token_into_form(monkeypatch):
	webpage_builder = _webpage_builder_module(monkeypatch)
	app = _test_app()
	with app.test_request_context("/integration/remove?token=query-token", base_url="https://example.test"):
		html = webpage_builder.build_integration_remove_page(None)

	assert 'value="query-token"' in html
	assert 'data-form-submit-route="/api/integration/remove"' in html
