from __future__ import annotations

import base64
import html
import json
import re
from pathlib import Path
from typing import Iterable

from werkzeug.exceptions import HTTPException

from sql.psql_interface import PSQLInterface
from util.fcr.file_config_reader import FileConfigReader
from util.webpage_builder import html_fragments
from util.webpage_builder.metrics_builder import METRICS_NAMES
from util.webpage_builder.page_api import (
	Box,
	CustomField,
	Form,
	FormAction,
	Heading,
	HiddenField,
	Link,
	Page,
	PageContext,
	PasswordField,
	RawHtml,
	SelectField,
	Stack,
	SubmitButton,
	Text,
	TextAreaField,
	TextField,
)
from util.webpage_builder.page_models import (
	load_admin_approvals_page_html,
	load_admin_dashboard_page_html,
	load_admin_users_page_html,
	load_discord_webhook_registration_model,
	load_landing_page_html,
	load_minecraft_page_model,
	load_profile_page_html,
	load_psql_interface_page_html,
	load_readme_text,
	load_verify_email_token_model,
)

_fcr: FileConfigReader | None = None
_interface: PSQLInterface | None = None


def _get_fcr() -> FileConfigReader:
	global _fcr
	if _fcr is None:
		_fcr = FileConfigReader()
	return _fcr


def _get_interface() -> PSQLInterface:
	global _interface
	if _interface is None:
		_interface = PSQLInterface()
	return _interface


def _page_context(user: dict | None, *, route_args: dict | None = None) -> PageContext:
	return PageContext.current(
		user=user,
		interface=_get_interface(),
		fcr=_get_fcr(),
		route_args=route_args,
	)


def _render(page: Page, ctx: PageContext) -> str:
	return page.render(ctx)


def _http_error(code: int, description: str) -> HTTPException:
	err = HTTPException()
	err.code = code
	err.description = description
	return err


def _admin_guard(ctx: PageContext, title: str) -> str | None:
	if not ctx.user:
		return build_error_page(None, _http_error(401, "Login required."))
	if not ctx.is_admin:
		return build_error_page(ctx.user, _http_error(403, "Admin access required."))
	_ = title
	return None


def _auth_guard(ctx: PageContext, description: str = "Login required.") -> str | None:
	if not ctx.user:
		return build_error_page(None, _http_error(401, description))
	return None


def _button_data_attrs(action: FormAction) -> dict[str, str]:
	data = {
		"submit-route": action.route,
		"submit-method": action.method.upper(),
	}
	if action.success_redirect is not None:
		data["success-redirect"] = action.success_redirect
	if action.failure_redirect is not None:
		data["failure-redirect"] = action.failure_redirect
	if action.refresh_on_success:
		data["success-refresh"] = "true"
	if action.refresh_on_failure:
		data["failure-refresh"] = "true"
	return data


def _submit_button(
	label: str,
	action: FormAction,
	*,
	class_name: str = "primary",
) -> SubmitButton:
	return SubmitButton(
		label=label,
		class_name=class_name,
		button_type="submit",
		data_attrs=_button_data_attrs(action),
	)


def _box_page(
	ctx: PageContext,
	*,
	title: str,
	box_children: Iterable[object],
	page_title: str | None = None,
	page_id: str | None = None,
	stylesheets: tuple[str, ...] = (),
	scripts: tuple[str, ...] = (),
	children_after_box: tuple[object, ...] = (),
) -> str:
	main_box = Box(children=tuple(box_children))
	if page_id:
		main_box = Stack(children=(main_box,), data_attrs={"page": page_id})
	children = (main_box, *children_after_box)
	return _render(
		Page(
			title=page_title or title,
			children=tuple(children),
			stylesheets=stylesheets,
			scripts=scripts,
		),
		ctx,
	)


def _centered_raw_page(
	ctx: PageContext,
	*,
	title: str,
	body_html: str,
	page_id: str | None = None,
	stylesheets: tuple[str, ...] = (),
	scripts: tuple[str, ...] = (),
) -> str:
	body = html_fragments.center_column(body_html)
	if page_id:
		body = f'<div data-page="{html.escape(page_id)}">{body}</div>'
	return _render(
		Page(
			title=title,
			children=(
				RawHtml(
					body,
				),
			),
			stylesheets=stylesheets,
			scripts=scripts,
		),
		ctx,
	)


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_MD_CODE_RE = re.compile(r"`([^`]+)`")
_MD_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_MD_LIST_ITEM_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>(?:[-*+])|(?:\d+\.))\s+(?P<body>.+)$")


def _md_inline(text: str) -> str:
	escaped = html.escape(text)
	escaped = _MD_IMAGE_RE.sub(
		lambda m: (
			f'<img src="{html.escape(m.group(2), quote=True)}" '
			f'alt="{m.group(1)}" loading="eager" fetchpriority="high">'
		),
		escaped,
	)
	escaped = _MD_LINK_RE.sub(lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', escaped)
	escaped = _MD_CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", escaped)
	escaped = _MD_BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", escaped)
	escaped = _MD_ITALIC_RE.sub(lambda m: f"<em>{m.group(1)}</em>", escaped)
	return escaped


def _indent_len(line: str) -> int:
	return len(line.expandtabs(4)) - len(line.lstrip(" \t").expandtabs(4))


def _list_type(marker: str) -> str:
	if marker.endswith(".") and marker[:-1].isdigit():
		return "ol"
	return "ul"


def _parse_list(lines: list[str], start: int, base_indent: int) -> tuple[str, int]:
	match = _MD_LIST_ITEM_RE.match(lines[start])
	if not match:
		return "", start
	list_type = _list_type(match.group("marker"))
	html_parts = [f"<{list_type}>"]
	index = start
	while index < len(lines):
		match = _MD_LIST_ITEM_RE.match(lines[index])
		if not match:
			break
		indent = _indent_len(lines[index])
		if indent != base_indent or _list_type(match.group("marker")) != list_type:
			break

		body = match.group("body").strip()
		html_parts.append(f"<li>{_md_inline(body)}")
		index += 1

		continuation: list[str] = []
		while index < len(lines):
			line = lines[index]
			if not line.strip():
				continuation.append("")
				index += 1
				continue

			next_item = _MD_LIST_ITEM_RE.match(line)
			if next_item and _indent_len(line) <= base_indent:
				break

			if _indent_len(line) > base_indent:
				continuation.append(line.strip())
				index += 1
				continue

			break

		if continuation:
			cont_text = " ".join(piece for piece in continuation if piece).strip()
			if cont_text:
				html_parts.append(f"<p>{_md_inline(cont_text)}</p>")

		if index < len(lines):
			next_item = _MD_LIST_ITEM_RE.match(lines[index])
			if next_item and _indent_len(lines[index]) > base_indent:
				nested_html, index = _parse_list(lines, index, _indent_len(lines[index]))
				if nested_html:
					html_parts.append(nested_html)

		html_parts.append("</li>")

	html_parts.append(f"</{list_type}>")
	return "".join(html_parts), index


def render_markdown(md_text: str) -> str:
	lines = md_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
	out: list[str] = []
	index = 0

	while index < len(lines):
		line = lines[index]
		stripped = line.strip()

		if not stripped:
			index += 1
			continue

		if stripped == "---":
			out.append("<hr>")
			index += 1
			continue

		if stripped.startswith("```"):
			code_lines: list[str] = []
			index += 1
			while index < len(lines) and not lines[index].strip().startswith("```"):
				code_lines.append(lines[index])
				index += 1
			if index < len(lines):
				index += 1
			code_html = html.escape("\n".join(code_lines))
			out.append(f"<pre><code>{code_html}</code></pre>")
			continue

		if stripped.startswith(">"):
			block_lines = [stripped[1:].lstrip()]
			index += 1
			while index < len(lines) and lines[index].strip().startswith(">"):
				block_lines.append(lines[index].strip()[1:].lstrip())
				index += 1
			out.append(f"<blockquote>{render_markdown(chr(10).join(block_lines))}</blockquote>")
			continue

		if stripped.startswith("#"):
			level = len(stripped) - len(stripped.lstrip("#"))
			level = max(1, min(level, 6))
			text = stripped[level:].strip()
			out.append(f"<h{level}>{_md_inline(text)}</h{level}>")
			index += 1
			continue

		list_match = _MD_LIST_ITEM_RE.match(line)
		if list_match:
			list_html, index = _parse_list(lines, index, _indent_len(line))
			if list_html:
				out.append(list_html)
				continue

		para_lines = [stripped]
		index += 1
		while index < len(lines):
			next_line = lines[index]
			next_stripped = next_line.strip()
			if not next_stripped:
				break
			if next_stripped.startswith(("```", ">", "#")) or _MD_LIST_ITEM_RE.match(next_line) or next_stripped == "---":
				break
			para_lines.append(next_stripped)
			index += 1

		out.append(f"<p>{_md_inline(' '.join(para_lines))}</p>")

	return "\n".join(out)


def _metric_graph_grid_html(metric_names: Iterable[str]) -> str:
	return (
		'<div class="metric-plot-grid">'
		+ "".join(
			f'<div class="metric-plot" data-metric="{html.escape(metric_name)}"></div>'
			for metric_name in metric_names
		)
		+ "</div>"
	)


def _load_popugame_rules_markdown() -> str:
	path = Path(__file__).resolve().parents[2] / "app" / "static" / "resources" / "popugame_rules.md"
	try:
		return path.read_text(encoding="utf-8", errors="replace")
	except Exception:
		return (
			"# PopuGame Rules\n\n"
			"- Take turns placing your token on an empty square the opponent has not claimed.\n"
			"- Make a line of 3+ tokens in any direction to trigger a claim.\n"
			"- Claimed squares can replace opponent claims, but tokens remain on board.\n"
			"- After 40 turns, higher score wins."
		)


def _frontend_sample(label: str, body_html: str, *, full: bool = False) -> str:
	extra_class = " frontend-sample--full" if full else ""
	return (
		f'<article class="frontend-sample{extra_class}">'
		f'<div class="frontend-sample__label">{html.escape(label)}</div>'
		f'<div class="frontend-sample__body">{body_html}</div>'
		"</article>"
	)


def _frontend_section(title: str, samples: list) -> str:
	"""samples: list of (label, body_html) or (label, body_html, full_width_bool)"""
	items_html = ""
	for item in samples:
		label, body_html = item[0], item[1]
		full = item[2] if len(item) > 2 else False
		items_html += _frontend_sample(label, body_html, full=full)
	return (
		'<section class="frontend-test-section">'
		f"<h2>{html.escape(title)}</h2>"
		'<div class="frontend-test-grid">'
		f"{items_html}"
		"</div>"
		"</section>"
	)


def _build_frontend_test_page_html() -> str:  # noqa: PLR0914
	# ── Typography & Tokens ───────────────────────────────────────────
	token_names = [
		"--dark_blue", "--objects", "--return", "--string",
		"--variables", "--brackets", "--error", "--form_text",
		"--background", "--border", "--contents", "--default",
	]
	color_tokens = (
		'<div class="ft-token-grid">'
		+ "".join(
			'<div class="ft-token">'
			f'<span class="ft-swatch" style="background:var({name})"></span>'
			f"<span>{name}</span>"
			"</div>"
			for name in token_names
		)
		+ "</div>"
	)
	badges = (
		'<div class="ft-row">'
		'<span class="badge badge--success">Success</span>'
		'<span class="badge badge--error">Error</span>'
		'<span class="badge badge--info">Info</span>'
		'<span class="badge badge--muted">Muted</span>'
		"</div>"
	)
	headings = (
		"<h1>Heading 1 (gradient via main h1)</h1>"
		"<h2>Heading 2</h2>"
		"<h3>Heading 3</h3>"
		"<p>Body text. <a href='#'>Link color.</a> <strong>Bold text.</strong></p>"
	)

	# ── Buttons ───────────────────────────────────────────────────────
	btn_variants = (
		'<div class="ft-row">'
		'<button class="btn">Default</button>'
		'<button class="btn btn--primary">Primary</button>'
		'<button class="btn btn--accent">Accent</button>'
		'<button class="btn btn--danger">Danger</button>'
		'<button class="btn btn--ghost">Ghost</button>'
		"</div>"
	)
	btn_sizes = (
		'<div class="ft-row">'
		'<button class="btn btn--primary btn--xs">XS</button>'
		'<button class="btn btn--primary btn--sm">SM</button>'
		'<button class="btn btn--primary">Default</button>'
		'<button class="btn btn--primary btn--lg">LG</button>'
		"</div>"
	)
	btn_pill = (
		'<div class="ft-row">'
		'<button class="btn btn--pill">Pill Default</button>'
		'<button class="btn btn--primary btn--pill">Pill Primary</button>'
		'<button class="btn btn--accent btn--pill btn--sm">Pill Accent SM</button>'
		"</div>"
	)
	btn_disabled = (
		'<div class="ft-row">'
		'<button class="btn" disabled>Disabled Default</button>'
		'<button class="btn btn--primary" disabled>Disabled Primary</button>'
		"</div>"
	)

	# ── Forms ─────────────────────────────────────────────────────────
	form_inputs = (
		'<form class="form">'
		'<div class="form-group"><label>Text</label>'
		'<input type="text" placeholder="Placeholder text"></div>'
		'<div class="form-group"><label>Password</label>'
		'<input type="password" placeholder="&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;"></div>'
		'<div class="form-group"><label>Email</label>'
		'<input type="email" placeholder="user@example.com"></div>'
		'<div class="form-group"><label>Disabled</label>'
		'<input type="text" placeholder="Disabled" disabled></div>'
		"</form>"
	)
	form_other = (
		'<form class="form">'
		'<div class="form-group"><label>Select</label>'
		'<select><option>Option A</option><option>Option B</option>'
		'<option>Option C</option></select></div>'
		'<div class="form-group"><label>Textarea</label>'
		'<textarea placeholder="Multi-line text..." style="min-height:4rem"></textarea></div>'
		'<div class="form-group">'
		'<label><input type="checkbox"> Accept terms and conditions</label></div>'
		"</form>"
	)
	form_messages = (
		'<div class="form-message" data-state="success" data-lines="1">'
		"Password updated successfully.</div>"
		'<div class="form-message" data-state="error" data-lines="1">'
		"Incorrect password. Please try again.</div>"
		'<div class="form-message" data-lines="1">Neutral informational message.</div>'
	)
	secret_field_sample = html_fragments.secret_field(
		"sk-test-example-key-1234567890abcdef",
		label="API Key",
	)
	scope_selector_sample = (
		'<form class="form"><div class="form-group">'
		+ html_fragments.api_scope_selector_input(
			[("read:data", "Read Data"), ("write:data", "Write Data"), ("admin:full", "Admin Full Access")],
		)
		+ "</div></form>"
	)

	# ── Auth / Login ──────────────────────────────────────────────────
	login_window = (
		'<div class="login-container">'
		'<div class="login-window">'
		'<h2 style="-webkit-text-fill-color:var(--default);background:none">Sign In</h2>'
		'<form class="form">'
		'<div class="form-group"><label>Email</label>'
		'<input type="email" placeholder="user@example.com"></div>'
		'<div class="form-group"><label>Password</label>'
		'<input type="password" placeholder="&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;"></div>'
		'<div class="form-group">'
		'<label class="checkbox-row"><span class="checkbox-label">Remember me</span>'
		'<input type="checkbox"></label></div>'
		'<button class="btn btn--primary" type="button">Log In</button>'
		"</form>"
		'<p class="link-row"><a href="#">Register new account</a></p>'
		"</div>"
		"</div>"
	)

	# ── Approval card (centering.css) ─────────────────────────────────
	approval_sample = (
		'<div class="centering-container" style="max-width:720px;">'
		+ html_fragments.approval_card(
			title="Audiobookshelf Access Request",
			subtitle="frontend@example.com",
			status_label="Pending",
			rows_html=(
				html_fragments.approval_row("Name", "Frontend Tester")
				+ html_fragments.approval_row("Email", "frontend@example.com")
				+ html_fragments.approval_row("Joined", "07 Mar 2026")
				+ html_fragments.approval_row(
					"Reason", "Testing the approval card in its real context.", full=True
				)
			),
			actions_html=html_fragments.approval_actions(
				"/api/audiobookshelf/approve",
				"/api/audiobookshelf/deny",
				"req-001",
			),
		)
		+ "</div>"
	)

	# ── Profile page ──────────────────────────────────────────────────
	active_sub = html_fragments.subscription_card(
		event_key="moderator.notifications",
		permission="admin:webhooks",
		description="Receive moderator action notifications in this channel.",
		date_str="01 Jan 2026",
		status_label="Active",
		is_active=True,
		unsubscribe_html=html_fragments.subscription_action(
			"unsubscribe", "sub-001", "/api/webhook/unsubscribe", "Unsubscribe"
		),
		resubscribe_html="",
	)
	inactive_sub = html_fragments.subscription_card(
		event_key="user.registrations",
		permission="admin:events",
		description="Notified when a new user registers.",
		date_str="15 Feb 2026",
		status_label="Inactive",
		is_active=False,
		unsubscribe_html="",
		resubscribe_html=html_fragments.subscription_action(
			"resubscribe", "sub-002", "/api/webhook/resubscribe", "Resubscribe"
		),
	)
	active_integration = html_fragments.integration_card(
		"discord-webhook", "int-001",
		"Discord Webhook",
		"webhook.zubekanov.com",
		"Approved · 2 subscriptions",
		html_fragments.integration_badge("Active")
		+ html_fragments.integration_delete_action(
			"discord-webhook", "int-001", "Discord Webhook", True
		),
		subscriptions_html=html_fragments.integration_subscriptions(
			"Subscriptions", active_sub + inactive_sub
		),
	)
	suspended_integration = html_fragments.integration_card(
		"audiobookshelf", "int-002",
		"Audiobookshelf",
		"abs.zubekanov.com",
		"Account suspended",
		html_fragments.integration_badge("Suspended")
		+ html_fragments.integration_enable_action(
			"audiobookshelf", "int-002", "Audiobookshelf", "Active"
		),
		status="suspended",
	)
	history_boxes = (
		[{"outcome": "win",   "tooltip": "Win vs Alice — 15 Mar 2026"}]
		+ [{"outcome": "loss",  "tooltip": "Loss vs Bob — 14 Mar 2026"}]
		+ [{"outcome": "win",   "tooltip": "Win vs Carol — 13 Mar 2026"}]
		+ [{"outcome": "draw",  "tooltip": "Draw vs Dave — 12 Mar 2026"}]
		+ [{"outcome": "win",   "tooltip": "Win vs Eve — 11 Mar 2026"}]
		+ [{"outcome": "loss",  "tooltip": "Loss vs Frank — 10 Mar 2026"}]
		+ [{"outcome": "empty", "tooltip": ""}] * 14
	)
	profile_full = html_fragments.profile_page_shell(
		html_fragments.profile_card(
			initials="JT",
			user_name="Frontend Tester",
			created_at="07 March 2026",
			email="frontend@example.com",
			badge_label="ADMIN",
			admin_line=html_fragments.profile_admin_line("07 March 2026"),
			panels_html=(
				html_fragments.profile_password_panel()
				+ html_fragments.profile_delete_panel()
			),
		)
		+ html_fragments.profile_integrations_header(
			"Integrations",
			"Manage your linked services and subscriptions.",
			active_integration + suspended_integration + html_fragments.integration_card_empty(),
		)
		+ html_fragments.profile_popugame_history_card(
			elo=1182,
			total_wr=58.3,
			wins=14,
			losses=9,
			draws=2,
			boxes=history_boxes,
		)
	)

	# ── Admin Dashboard ───────────────────────────────────────────────
	admin_full = html_fragments.admin_dashboard(
		html_fragments.admin_dashboard_section(
			"Approvals",
			html_fragments.admin_card(
				"/admin/audiobookshelf-approvals",
				html_fragments.admin_card_meta("Audiobookshelf", html_fragments.admin_badge_count(3)),
				"Audiobookshelf",
				"Review pending access requests.",
			)
			+ html_fragments.admin_card(
				"/admin/minecraft-approvals",
				html_fragments.admin_card_meta("Minecraft", html_fragments.admin_badge_count(0)),
				"Minecraft",
				"Review whitelist requests.",
			)
			+ html_fragments.admin_card(
				"/admin/discord-webhook-approvals",
				html_fragments.admin_card_meta("Discord Webhooks", html_fragments.admin_badge_count(None)),
				"Discord Webhooks",
				"Review webhook registrations.",
			)
			+ html_fragments.admin_card(
				"/admin/api-access-approvals",
				html_fragments.admin_card_meta("API Access", html_fragments.admin_badge_count(1)),
				"API Access",
				"Review developer API applications.",
			),
		)
		+ html_fragments.admin_dashboard_section(
			"Tools",
			html_fragments.admin_card(
				"/admin/users",
				html_fragments.admin_card_meta("Users", ""),
				"User Management",
				"Manage accounts and roles.",
			)
			+ html_fragments.admin_card(
				"/psql-interface",
				html_fragments.admin_card_meta("Database", ""),
				"PSQL Interface",
				"Direct table access and editing.",
			)
			+ html_fragments.admin_card(
				"/admin/email-debug",
				html_fragments.admin_card_meta("Email", ""),
				"Email Debug",
				"Preview and test email templates.",
			),
			compact=True,
		)
	)

	# ── Admin Users ───────────────────────────────────────────────────
	admin_users_full = html_fragments.admin_users_shell(
		html_fragments.admin_user_card(
			user_id="usr-001",
			name="Frontend Tester",
			email="frontend@example.com",
			meta_html=(
				html_fragments.admin_user_meta_row("Joined", "07 Mar 2026")
				+ html_fragments.admin_user_meta_row("Last login", "06 Apr 2026")
				+ html_fragments.admin_user_meta_row("Sessions", "3 active")
			),
			badge_html=html_fragments.admin_user_badge("ADMIN"),
			actions_html=html_fragments.admin_user_actions(
				html_fragments.admin_user_action_button("toggle-admin", "usr-001", "Remove Admin")
				+ html_fragments.admin_user_action_button(
					"delete", "usr-001", "Delete User", is_danger=True
				)
			),
			integrations_html=html_fragments.admin_user_integrations(
				html_fragments.integration_card(
					"discord-webhook", "int-admin-001",
					"Discord Webhook",
					"webhook.zubekanov.com",
					"Approved",
					html_fragments.integration_badge("Active"),
				)
			),
			role_label="admin",
		)
		+ html_fragments.admin_user_card(
			user_id="usr-002",
			name="Regular Member",
			email="member@example.com",
			meta_html=(
				html_fragments.admin_user_meta_row("Joined", "01 Jan 2026")
				+ html_fragments.admin_user_meta_row("Last login", "03 Apr 2026")
			),
			badge_html=html_fragments.admin_user_badge("MEMBER"),
			actions_html=html_fragments.admin_user_actions(
				html_fragments.admin_user_action_button("toggle-admin", "usr-002", "Make Admin")
				+ html_fragments.admin_user_action_button(
					"delete", "usr-002", "Delete User", is_danger=True
				)
			),
			integrations_html=html_fragments.admin_user_integrations(
				html_fragments.integration_card_empty("No linked integrations.")
			),
		)
	)

	# ── Metrics ───────────────────────────────────────────────────────
	kpi_html = "".join(
		html_fragments.metrics_kpi_card(key, label)
		for key, label in METRICS_NAMES.items()
	)
	metrics_full = (
		html_fragments.metrics_dashboard_open()
		+ kpi_html
		+ html_fragments.metrics_dashboard_between_sections()
		+ _metric_graph_grid_html(METRICS_NAMES.keys())
		+ html_fragments.metrics_dashboard_close()
	)

	# ── Minecraft ─────────────────────────────────────────────────────
	minecraft = html_fragments.minecraft_status_card("mc.zubekanov.com")

	# ── Landing Nav ───────────────────────────────────────────────────
	landing = load_landing_page_html(user=None, is_admin=False, fcr=_get_fcr())

	# ── Assemble ──────────────────────────────────────────────────────
	sections = [
		("Typography & Tokens", [
			("Headings", headings),
			("Color tokens", color_tokens),
			("Badges (global.css)", badges),
		]),
		("Buttons", [
			("Variants", btn_variants),
			("Sizes", btn_sizes),
			("Pill", btn_pill),
			("Disabled", btn_disabled),
		]),
		("Forms", [
			("Text inputs", form_inputs),
			("Select, Textarea & Checkbox", form_other),
			("Form messages", form_messages),
			("Secret field", secret_field_sample),
			("Scope selector", scope_selector_sample),
		]),
		("Auth — Login Window", [
			("login-window in login-container", login_window, True),
		]),
		("Approval Cards", [
			("approval-card in centering-container", approval_sample, True),
		]),
		("Profile Page", [
			("Full profile page with integrations and history", profile_full, True),
		]),
		("Admin Dashboard", [
			("Full admin dashboard", admin_full, True),
		]),
		("Admin Users", [
			("Admin user list — admin card + member card", admin_users_full, True),
		]),
		("Metrics", [
			("Full metrics shell — all KPIs and chart placeholders", metrics_full, True),
		]),
		("Minecraft", [
			("Server status card", minecraft),
		]),
		("Landing Nav", [
			("Landing page as shown to unauthenticated users", landing, True),
		]),
	]
	return (
		'<div class="frontend-test-page">'
		"<h1>Frontend Test Page</h1>"
		'<p class="frontend-test-intro">'
		"All UI components, shown in the contexts in which they appear to users."
		"</p>"
		f"{''.join(_frontend_section(title, samples) for title, samples in sections)}"
		"</div>"
	)


def is_admin_user(user: dict | None) -> bool:
	if not user:
		return False
	try:
		return bool(_get_interface().is_admin(user.get("id")))
	except Exception:
		return False


def build_test_page(user: dict | None) -> str:
	ctx = _page_context(user)
	return _render(
		Page(
			title="Test Page",
			children=(
				RawHtml("<h1>Test Page</h1><p>Page API wiring is active.</p>"),
			),
		),
		ctx,
	)


def build_readme_page(user: dict | None) -> str:
	ctx = _page_context(user)
	readme_html = (
		'<div class="centering-container" style="max-width:860px; padding:2rem 2rem; margin:0 auto;">'
		"<h1>README.md</h1>"
		f'<div class="markdown-block">{render_markdown(load_readme_text())}</div>'
		"</div>"
	)
	return _render(
		Page(
			title="README.md",
			children=(RawHtml(readme_html),),
			stylesheets=("/static/css/centering.css", "/static/css/markdown.css"),
		),
		ctx,
	)


def build_empty_landing_page(user: dict | None) -> str:
	ctx = _page_context(user)
	page_html = load_landing_page_html(user=user, is_admin=ctx.is_admin, fcr=_get_fcr())
	return _render(
		Page(
			title="Home",
			children=(Stack(children=(RawHtml(page_html),), data_attrs={"page": "landing"}),),
			stylesheets=("/static/css/landing_nav.css",),
		),
		ctx,
	)


def build_profile_page(user: dict | None) -> str:
	ctx = _page_context(user)
	auth_error = _auth_guard(ctx)
	if auth_error:
		return auth_error
	profile_model = load_profile_page_html(_get_interface(), ctx.user, is_admin=ctx.is_admin)
	return _render(
		Page(
			title=profile_model["title"],
			children=(Stack(children=(RawHtml(profile_model["body_html"]),), data_attrs={"page": "profile"}),),
			stylesheets=("/static/css/profile.css",),
			scripts=(
				"/static/js/copy_tooltip.js",
				"/static/js/profile_integrations.js",
				"/static/js/profile_popugame_history.js",
			),
		),
		ctx,
	)


def build_login_page(user: dict | None) -> str:
	ctx = _page_context(user)
	action = FormAction(
		route="/login",
		method="POST",
		success_redirect="/profile",
	)
	form = Form(
		form_id="login-form",
		fields=(
			TextField(label="Email", name="email", placeholder="Email"),
			PasswordField(label="Password", name="password", placeholder="Password"),
			CustomField(
				inner_html='<label class="checkbox-row"><span class="checkbox-label">Remember me</span><input type="checkbox" name="remember_me"></label>',
				group_class="form-group",
			),
		),
		submit_buttons=(
			_submit_button("Log in", action),
		),
		action=action,
	)
	return _box_page(
		ctx,
		title="Login",
		page_id="login",
		box_children=(
			Heading("Login", 2),
			form,
			Link("Register new account", "/register", wrap_in_paragraph=True),
			Link("Forgot password", "/forgot-password", wrap_in_paragraph=True),
		),
	)


def build_register_page(user: dict | None) -> str:
	ctx = _page_context(user)
	action = FormAction(
		route="/register",
		method="POST",
		success_redirect="/verify-email",
	)
	form = Form(
		form_id="register-form",
		fields=(
			SelectField(
				label="How did you discover my website?",
				name="referral_source",
				options=(
					("friend", "Friend or colleague"),
					("github", "GitHub"),
					("resume", "Resume / CV"),
					("linkedin", "LinkedIn or online profile"),
					("other", "Other"),
				),
			),
			TextField(label="First Name", name="first_name", placeholder="First Name"),
			TextField(label="Last Name", name="last_name", placeholder="Last Name"),
			TextField(label="Email", name="email", placeholder="Email"),
			PasswordField(label="Password", name="password", placeholder="Password"),
			PasswordField(label="Repeat Password", name="repeat_password", placeholder="Repeat password"),
		),
		submit_buttons=(
			_submit_button("Create account", action),
		),
		action=action,
	)
	return _box_page(
		ctx,
		title="Register",
		page_id="register",
		box_children=(
			Heading("Register", 2),
			form,
			Link("Login to existing account", "/login", wrap_in_paragraph=True),
		),
	)


def _contact_fields_for_user(user: dict | None, *, email_name: str = "email") -> tuple[object, ...]:
	if user:
		return (
			HiddenField("first_name", str(user.get("first_name", ""))),
			HiddenField("last_name", str(user.get("last_name", ""))),
			HiddenField(email_name, str(user.get("email", ""))),
		)
	return (
		TextField(label="First Name", name="first_name", placeholder="First Name"),
		TextField(label="Last Name", name="last_name", placeholder="Last Name"),
		TextField(label="Email" if email_name == "email" else "Contact Email", name=email_name, placeholder="you@example.com"),
	)


def build_audiobookshelf_registration_page(user: dict | None) -> str:
	ctx = _page_context(user)
	action = FormAction(
		route="/audiobookshelf-registration",
		method="POST",
		success_redirect="/",
	)
	form = Form(
		form_id="audiobookshelf-registration-form",
		fields=(
			*_contact_fields_for_user(ctx.user),
			TextAreaField(
				label="Additional Information",
				name="additional_info",
				placeholder="Enter any additional information here...",
			),
		),
		submit_buttons=(
			_submit_button("Submit Registration", action),
		),
		action=action,
	)
	return _box_page(
		ctx,
		title="Audiobookshelf Registration",
		page_id="audiobookshelf-registration",
		box_children=(
			Heading("Audiobookshelf Registration", 2),
			form,
			Text("You will receive a follow-up email with further instructions if your registration is approved."),
		),
	)


def build_api_access_application_page(user: dict | None) -> str:
	ctx = _page_context(user)
	action = FormAction(
		route="/api-access-application",
		method="POST",
		success_redirect="/",
	)
	scope_selector = CustomField(
		inner_html=html_fragments.api_scope_selector_input([
			("metrics.read", "metrics.read"),
			("webhook.write", "webhook.write"),
			("webhook.read", "webhook.read"),
			("admin.api", "admin.api"),
		]),
		group_class="form-group",
		scripts=("/static/js/api_scope_selector.js",),
	)
	form = Form(
		form_id="api-access-application-form",
		fields=(
			*_contact_fields_for_user(ctx.user),
			SelectField(
				label="Principal Type",
				name="principal_type",
				options=(
					("service", "Service / system"),
					("user", "User principal"),
				),
			),
			TextField(label="Service Name", name="service_name", placeholder="Service name"),
			scope_selector,
			TextAreaField(label="Use Case", name="use_case", placeholder="Describe your request."),
		),
		submit_buttons=(
			_submit_button("Submit Application", action),
		),
		action=action,
	)
	return _box_page(
		ctx,
		title="API Access Application",
		page_id="api-access-application",
		box_children=(
			Heading("API Access Application", 2),
			form,
		),
	)


def build_discord_webhook_registration_page(user: dict | None) -> str:
	ctx = _page_context(user)
	model = load_discord_webhook_registration_model(_get_interface(), ctx.user, is_admin=ctx.is_admin)
	action = FormAction(
		route="/discord-webhook/verify",
		method="POST",
		success_redirect="",
	)

	webhook_fields: list[object] = []
	if model["webhook_pairs"]:
		options_json = json.dumps(model["webhook_pairs"])
		options_b64 = base64.b64encode(options_json.encode("utf-8")).decode("ascii")
		webhook_fields.extend((
			CustomField(
				inner_html=html_fragments.webhook_selector_input(
					label="Webhook Name",
					input_id="name",
					name="name",
					placeholder="My Webhook",
					data_kind="name",
				),
				group_class="form-group",
			),
			CustomField(
				inner_html=html_fragments.webhook_selector_input(
					label="Webhook URL",
					input_id="webhook_url",
					name="webhook_url",
					placeholder="https://discord.com/api/webhooks/...",
					data_kind="url",
				),
				group_class="form-group",
			),
			RawHtml(
				html_fragments.webhook_options_data_script(options_b64),
				scripts=("/static/js/webhook_selector.js",),
			),
		))
	else:
		webhook_fields.extend((
			TextField(label="Webhook Name", name="name", placeholder="My Webhook"),
			TextField(label="Webhook URL", name="webhook_url", placeholder="https://discord.com/api/webhooks/..."),
		))

	form_fields = list(webhook_fields)
	if model["contact_required"]:
		form_fields.extend(_contact_fields_for_user(None, email_name="contact_email"))
	form = Form(
		form_id="discord-webhook-registration-form",
		fields=(
			*tuple(form_fields),
			SelectField(
				label="Event Key",
				name="event_key",
				options=tuple((value, label) for value, label in model["event_options"]),
			),
			Text("Click to send a verification code to your webhook."),
		),
		submit_buttons=(
			_submit_button("Send Verification Code", action),
		),
		action=action,
	)
	return _box_page(
		ctx,
		title="Discord Webhook Registration",
		page_id="discord-webhook-registration",
		box_children=(
			Heading("Discord Webhook Registration", 2),
			form,
		),
	)


def build_discord_webhook_verify_page(user: dict | None) -> str:
	ctx = _page_context(user)
	verification_id = ctx.query_value("vid")
	code = ctx.query_value("code")
	action = FormAction(
		route="/discord-webhook/verify/submit",
		method="POST",
		success_redirect="/discord-webhook/verified",
	)
	form = Form(
		form_id="discord-webhook-verify-form",
		fields=(
			TextField(
				label="Verification Code",
				name="verification_code",
				placeholder="6-digit code",
				value=code,
			),
			HiddenField("verification_id", verification_id),
		),
		submit_buttons=(
			_submit_button("Submit Code", action),
		),
		action=action,
	)
	return _box_page(
		ctx,
		title="Webhook Verification",
		page_title="Webhook Verification",
		page_id="discord-webhook-verify",
		box_children=(
			Heading("Enter Verification Code", 2),
			form,
			Text("If you opened this page from the webhook link, the ID is prefilled."),
			RawHtml(html_fragments.webhook_verify_autosubmit_script()),
		),
	)


def build_discord_webhook_verified_page(user: dict | None) -> str:
	ctx = _page_context(user)
	status = ctx.query_value("status").strip().lower()
	if status == "approved":
		title = "Webhook Approved"
		message = html_fragments.paragraph("Your webhook request was automatically approved.")
	elif status == "reactivated":
		title = "Subscription Reactivated"
		message = html_fragments.paragraph("Your existing webhook subscription has been reactivated.")
	elif status == "submitted":
		title = "Request Submitted"
		message = html_fragments.paragraph("Your webhook registration has been sent for approval.")
	else:
		title = "Webhook Submitted"
		message = html_fragments.paragraph("Your webhook registration has been sent for approval.")
	return _box_page(
		ctx,
		title=title,
		page_id="discord-webhook-verified",
		box_children=(
			Heading(title, 2),
			RawHtml(message),
			RawHtml(html_fragments.return_home()),
		),
	)


def build_verify_email_page(user: dict | None) -> str:
	ctx = _page_context(user)
	return _box_page(
		ctx,
		title="Verify Your Email",
		page_id="verify-email",
		box_children=(
			Heading("Verify Your Email", 2),
			Text("Thank you for registering! Please check your email for a verification link to complete your registration."),
		),
	)


def build_verify_email_token_page(user: dict | None, token: str) -> str:
	ctx = _page_context(user, route_args={"token": token})
	model = load_verify_email_token_model(_get_interface(), token)
	return _box_page(
		ctx,
		title=model["page_title"],
		page_id="verify-email-token",
		box_children=(
			Heading(model["page_title"], 2),
			RawHtml(model["message_html"]),
		),
	)


def build_server_metrics_page(user: dict | None) -> str:
	ctx = _page_context(user)
	kpi_html = "".join(
		html_fragments.metrics_kpi_card(key, label)
		for key, label in METRICS_NAMES.items()
	)
	body_html = (
		html_fragments.metrics_dashboard_open()
		+ kpi_html
		+ html_fragments.metrics_dashboard_between_sections()
		+ _metric_graph_grid_html(METRICS_NAMES.keys())
		+ html_fragments.metrics_dashboard_close()
	)
	return _render(
		Page(
			title="Server Metrics",
			children=(Stack(children=(RawHtml(body_html),), data_attrs={"page": "metrics"}),),
			stylesheets=(
				"/static/css/metrics_dashboard.css",
				"/static/css/plotly.css",
			),
			scripts=(
				"https://cdn.plot.ly/plotly-2.35.2.min.js",
				"/static/js/plotly.js",
			),
		),
		ctx,
	)


def build_stock_viewer_page(user: dict | None) -> str:
	ctx = _page_context(user)
	body_html = """
	<section class="stocks-page" data-stock-viewer data-default-timeframe="5Min" data-rolling-window-days="7">
		<div class="stocks-page__search">
			<h2>Stock Search</h2>
			<p>Search US equities and view historical 5-minute prices (rolling 7-day window).</p>
			<label for="stock-search-input">Ticker or company name</label>
			<input id="stock-search-input" type="search" autocomplete="off" placeholder="AAPL or Apple">
			<div id="stock-search-status" class="stocks-page__status">Type to search...</div>
			<ul id="stock-search-results" class="stocks-page__results" aria-live="polite"></ul>
		</div>
		<div class="stocks-page__viewer">
			<div class="stocks-page__header">
				<h3 id="stock-symbol-label">No symbol selected</h3>
				<div id="stock-name-label" class="stocks-page__name"></div>
				<div id="stock-price-label" class="stocks-page__price">-</div>
			</div>
			<div class="stocks-page__controls">
				<div class="stocks-page__ranges" role="group" aria-label="Range">
					<button class="stock-range-btn" data-range="1D" type="button">1D</button>
					<button class="stock-range-btn is-active" data-range="1W" type="button">1W</button>
				</div>
				<span class="stocks-page__timeframe-pill">Timeframe: 5Min | Retention: 7D</span>
			</div>
			<div id="stock-chart-wrap" class="stocks-page__chart-wrap">
				<div id="stock-plot" class="stocks-page__plot" role="img" aria-label="Stock price chart"></div>
			</div>
			<div id="stock-view-status" class="stocks-page__status">Select a symbol to load prices.</div>
		</div>
	</section>
	"""
	return _render(
		Page(
			title="Stock Search",
			children=(
				RawHtml(body_html),
				RawHtml(html_fragments.return_home()),
			),
			stylesheets=("/static/css/stocks.css",),
			scripts=(
				"https://cdn.plot.ly/plotly-2.35.2.min.js",
				"/static/js/stocks.js",
			),
		),
		ctx,
	)


def build_reset_password_page(user: dict | None) -> str:
	ctx = _page_context(user)
	action = FormAction(
		route="/reset-password",
		method="POST",
		success_redirect="/",
	)
	form = Form(
		form_id="reset-password-form",
		fields=(
			TextField(label="Email", name="email", placeholder="Email"),
		),
		submit_buttons=(
			_submit_button("Send Reset Link", action),
		),
		action=action,
	)
	return _box_page(
		ctx,
		title="Reset Password",
		page_id="reset-password",
		box_children=(
			Heading("Reset Password", 2),
			form,
		),
	)


def build_delete_account_page(user: dict | None) -> str:
	ctx = _page_context(user)
	auth_error = _auth_guard(ctx)
	if auth_error:
		return auth_error
	action = FormAction(
		route="/delete-account",
		method="POST",
		success_redirect="/",
	)
	form = Form(
		form_id="delete-account-form",
		fields=(
			RawHtml(
				html_fragments.paragraph_with_bold(
					"Please enter your password to confirm deletion of the account for ",
					ctx.user["email"],
					":",
				)
			),
			PasswordField(label="Confirm Password", name="password", placeholder="Password"),
		),
		submit_buttons=(
			_submit_button("Delete Account", action, class_name="danger"),
		),
		action=action,
	)
	return _box_page(
		ctx,
		title="Delete Account",
		page_id="delete-account",
		box_children=(
			Heading("Delete Account", 2),
			Text("Deleting your account is irreversible."),
			form,
		),
	)


def build_minecraft_page(user: dict | None) -> str:
	ctx = _page_context(user)
	model = load_minecraft_page_model(_get_interface(), _get_fcr(), ctx.user)
	action = FormAction(
		route="/minecraft-registration",
		method="POST",
		success_redirect="",
	)
	form_fields = list(_contact_fields_for_user(ctx.user))
	form_fields.extend((
		TextField(label="Minecraft Username", name="mc_username", placeholder="Your in-game name"),
		SelectField(
			label="Who are you?",
			name="who_are_you",
			options=(
				("friend", "Friend or family"),
				("colleague", "Colleague / work contact"),
				("community", "Community member"),
				("other", "Other"),
			),
		),
		TextAreaField(
			label="Additional Information",
			name="additional_info",
			placeholder="Tell us a bit about yourself...",
		),
	))
	form = Form(
		form_id="minecraft-registration-form",
		fields=tuple(form_fields),
		submit_buttons=(
			_submit_button("Submit Request", action),
		),
		action=action,
	)
	return _render(
		Page(
			title="Minecraft Server",
			children=(
				Stack(
					data_attrs={"page": "minecraft"},
					children=(
						RawHtml(html_fragments.minecraft_status_card(model["host"])),
						RawHtml(html_fragments.minecraft_whitelist_banner(model["is_whitelisted"], model["whitelist_username"] or "")),
						RawHtml(html_fragments.minecraft_registration_wrap_open(model["is_whitelisted"])),
						Box(
							container_class="login-container no-glow",
							children=(
								Heading("Minecraft Whitelist Request", 3),
								form,
							),
						),
						RawHtml(html_fragments.minecraft_registration_wrap_close()),
						RawHtml(html_fragments.return_home()),
					),
				),
			),
			stylesheets=("/static/css/minecraft.css",),
			scripts=(
				"/static/js/copy_tooltip.js",
				"/static/js/minecraft_status.js",
			),
		),
		ctx,
	)


def build_popugame_page(user: dict | None, *, game_code: str | None = None) -> str:
	ctx = _page_context(user, route_args={"game_code": game_code})
	rules_html = render_markdown(_load_popugame_rules_markdown())
	code_attr = f' data-popugame-code="{html.escape(game_code)}"' if game_code else ""
	share_panel = ""
	if game_code:
		link = f"/popugame/{game_code}"
		link_safe = html.escape(link)
		code_safe = html.escape(game_code)
		share_panel = f"""
		<div class="popugame__share-panel" data-popugame-share-panel hidden>
			<div class="popugame__share-title">Waiting for opponent</div>
			<div class="popugame__share-subtitle">Share this link or code to invite someone.</div>
			<div class="popugame__share-grid">
				<div class="popugame__share-field">
					<label>Game Link</label>
					<span class="minecraft-host-chip popugame__copy-chip" data-popugame-copy-chip data-popugame-copy-kind="link">
						<span class="minecraft-host-text" data-popugame-share-link>{link_safe}</span>
						<button class="minecraft-host-copy" type="button" data-popugame-copy-btn aria-label="Copy game link">
							<img src="/static/img/copy.png" alt="">
							<span class="minecraft-host-tooltip" data-popugame-tooltip aria-hidden="true">Copied</span>
						</button>
					</span>
				</div>
				<div class="popugame__share-field">
					<label>Game Code</label>
					<span class="minecraft-host-chip popugame__copy-chip" data-popugame-copy-chip data-popugame-copy-kind="code">
						<span class="minecraft-host-text" data-popugame-share-code>{code_safe}</span>
						<button class="minecraft-host-copy" type="button" data-popugame-copy-btn aria-label="Copy game code">
							<img src="/static/img/copy.png" alt="">
							<span class="minecraft-host-tooltip" data-popugame-tooltip aria-hidden="true">Copied</span>
						</button>
					</span>
				</div>
			</div>
		</div>
		"""

	controls_html = """
	<div class="popugame__controls">
		<div class="popugame__controls-group popugame__controls-group--play">
			<button class="btn popugame__btn btn--ghost" type="button" data-popugame-rules>Rules</button>
			<button class="btn popugame__btn btn--accent" type="button" data-popugame-undo disabled>Undo Move</button>
			<button class="btn popugame__btn" type="button" data-popugame-reset>Reset Board</button>
		</div>
		<div class="popugame__controls-group popugame__controls-group--multi">
			<button class="btn popugame__btn btn--primary" type="button" data-popugame-host>Host Multiplayer Game</button>
			<div class="popugame__join-row">
				<input class="popugame__join-input" type="text" data-popugame-join-input maxlength="6" placeholder="Game code" autocomplete="off" spellcheck="false">
				<button class="btn popugame__btn" type="button" data-popugame-join>Join</button>
			</div>
		</div>
	</div>
	"""
	if game_code:
		controls_html = """
		<div class="popugame__controls">
			<div class="popugame__controls-group popugame__controls-group--play">
				<button class="btn popugame__btn btn--ghost" type="button" data-popugame-rules>Rules</button>
				<button class="btn popugame__btn btn--danger" type="button" data-popugame-concede>Concede Game</button>
				<button class="btn popugame__btn btn--danger" type="button" data-popugame-abandon hidden>Abandon Match</button>
				<a class="btn popugame__btn btn--ghost" href="/popugame">&#8592; PopuGame</a>
			</div>
			<div class="popugame__controls-group popugame__controls-group--multi">
				<button class="btn popugame__btn btn--primary" type="button" data-popugame-host data-popugame-postgame hidden>Host Multiplayer Game</button>
				<button class="btn popugame__btn" type="button" data-popugame-join data-popugame-postgame hidden>Join via Code</button>
			</div>
		</div>
		"""

	body_html = f"""
	<div class="popugame-shell" data-popugame data-size="9" data-turn-limit="40"{code_attr}>
		<div class="popugame__header">
			<div class="popugame__center">
				<div class="popugame__turnwrap">
					<div class="popugame__turnlabel">Turns Left: <span data-popugame-turn>40</span></div>
					<div class="popugame__turnbar" data-popugame-turnbar>
						<div class="popugame__turnbar-track" data-popugame-turn-track></div>
					</div>
				</div>
			</div>
			<div class="popugame__scorebox" aria-label="Score and status">
				<div class="popugame__statusbox">
					<div class="popugame__status" data-popugame-status>Player 1 to move</div>
				</div>
				<div class="popugame__names">
					<span class="popugame__name popugame__name--p0" data-popugame-name="0">Player 1</span>
					<span class="popugame__name-sep">vs</span>
					<span class="popugame__name popugame__name--p1" data-popugame-name="1">Player 2</span>
				</div>
				<div class="popugame__scoreline">
					<span class="popugame__score popugame__score--p0" data-popugame-score="0">0</span>
					<span class="popugame__score-sep">:</span>
					<span class="popugame__score popugame__score--p1" data-popugame-score="1">0</span>
				</div>
				<div class="popugame__score-label">claimed cells</div>
			</div>
		</div>
		{share_panel}
		<div class="popugame__board" data-popugame-board aria-label="PopuGame board"></div>
		{controls_html}
		<div class="popugame__backdrop" data-popugame-modal aria-hidden="true">
			<div class="popugame__modal popugame__modal--rules" role="dialog" aria-modal="true" aria-labelledby="popugame-rules-title">
				<div class="popugame__modal-header">
					<h3 id="popugame-rules-title">PopuGame Rules</h3>
					<button class="popugame__close" type="button" data-popugame-close aria-label="Close rules">×</button>
				</div>
				<div class="popugame__modal-body">
					<div class="markdown-block popugame__rules-markdown">{rules_html}</div>
				</div>
			</div>
		</div>
		<div class="popugame__backdrop" data-popugame-dialog aria-hidden="true">
			<div class="popugame__modal" role="dialog" aria-modal="true" aria-labelledby="popugame-dialog-title">
				<div class="popugame__modal-header">
					<h3 id="popugame-dialog-title" data-popugame-dialog-title>Notice</h3>
					<button class="popugame__close" type="button" data-popugame-dialog-close aria-label="Close dialog">×</button>
				</div>
				<div class="popugame__modal-body">
					<div data-popugame-dialog-body></div>
					<div class="popugame__dialog-actions">
						<button class="btn" type="button" data-popugame-dialog-cancel>Cancel</button>
						<button class="btn btn--primary" type="button" data-popugame-dialog-confirm>OK</button>
					</div>
				</div>
			</div>
		</div>
		<div class="popugame__backdrop" data-popugame-endgame aria-hidden="true">
			<div class="popugame__modal popugame__endgame-modal" role="dialog" aria-modal="true" aria-labelledby="popugame-endgame-title">
				<div class="popugame__modal-header">
					<h3 id="popugame-endgame-title">Game Over</h3>
					<button class="popugame__close" type="button" data-popugame-endgame-close aria-label="Close end game popup">×</button>
				</div>
				<div class="popugame__modal-body">
					<p class="popugame__endgame-result" data-popugame-endgame-result>Game over</p>
					<p class="popugame__endgame-meta" data-popugame-endgame-score>Final score: 0 : 0</p>
					<p class="popugame__endgame-meta" data-popugame-endgame-reason>Reason: turn limit</p>
					<div class="popugame__endgame-elo" data-popugame-endgame-elo hidden>
						<p data-popugame-endgame-elo-p0></p>
						<p data-popugame-endgame-elo-p1></p>
					</div>
					<div class="popugame__dialog-actions">
						<button class="btn btn--primary" type="button" data-popugame-endgame-playagain>Play Again</button>
						<button class="btn" type="button" data-popugame-endgame-host hidden>New Game (Host)</button>
						<button class="btn" type="button" data-popugame-endgame-join hidden>Join Game</button>
						<button class="btn btn--primary" type="button" data-popugame-endgame-dismiss>Close</button>
					</div>
				</div>
			</div>
		</div>
	</div>
	{html_fragments.return_home()}
	"""
	return _render(
		Page(
			title="PopuGame",
			children=(RawHtml(body_html),),
			stylesheets=(
				"/static/css/popugame.css",
				"/static/css/minecraft.css",
				"/static/css/markdown.css",
			),
			scripts=(
				"/static/js/copy_tooltip.js",
				"/static/js/popugame.js",
			),
		),
		ctx,
	)


def build_popugame_landing_page(user: dict | None) -> str:
	ctx = _page_context(user)
	body_html = """
	<div class="popugame-landing">
		<div class="popugame-landing__header">
			<h1 class="popugame-landing__title">Popugame Lobby</h1>
		</div>
		<div class="popugame-landing__body">
			<div class="popugame-landing__leaderboard-panel">
				<div class="popugame-landing__panel-header">
					<span class="popugame-landing__panel-title">Leaderboard</span>
				</div>
				<div class="popugame-leaderboard-list" data-pg-leaderboard-list>
					<div class="popugame-landing__loading">Loading…</div>
				</div>
			</div>
			<div class="popugame-landing__right">
				<div class="popugame-landing__quickplay">
					<div class="popugame-landing__panel-title">Play</div>
					<a href="/popugame/local" class="btn btn--primary popugame-landing__play-btn">Play Locally</a>
					<button class="btn popugame-landing__play-btn" id="pg-host-private" type="button">Host Private Game</button>
					<button class="btn btn--accent popugame-landing__play-btn" id="pg-host-public" type="button">Start Public Game</button>
					<div class="popugame__join-row popugame-landing__join-row">
						<input class="popugame__join-input" type="text" id="pg-join-input" maxlength="6" placeholder="Game code" autocomplete="off" spellcheck="false">
						<button class="btn" id="pg-join-btn" type="button">Join</button>
					</div>
				</div>
				<div class="popugame-landing__lobby">
					<div class="popugame-landing__panel-header">
						<span class="popugame-landing__panel-title">Public Lobby</span>
						<button class="btn btn--ghost popugame-landing__refresh-btn" id="pg-lobby-refresh" type="button" aria-label="Refresh lobby">&#8635;</button>
					</div>
					<div data-pg-lobby-list>
						<div class="popugame-landing__loading">Loading…</div>
					</div>
				</div>
			</div>
		</div>
		<div class="popugame-landing__history">
			<div class="popugame-landing__panel-header">
				<span class="popugame-landing__panel-title">Recent Games</span>
			</div>
			<div data-pg-history-list>
				<div class="popugame-landing__loading">Loading…</div>
			</div>
		</div>
	</div>
	"""
	return _render(
		Page(
			title="PopuGame",
			children=(RawHtml(body_html, boot_data={
				"is_logged_in": bool(user),
				"user_id": str(user.get("id")) if user else None,
			}),),
			stylesheets=(
				"/static/css/popugame.css",
			),
			scripts=(
				"/static/js/copy_tooltip.js",
				"/static/js/popugame_landing.js",
			),
		),
		ctx,
	)


def build_popugame_replay_page(user: dict | None, *, code: str) -> str:
	ctx = _page_context(user)
	code_safe = html.escape(code.upper())
	body_html = f"""
	<div class="popugame-replay" data-pg-replay data-pg-replay-code="{code_safe}">
		<div class="popugame-replay__header">
			<h2 class="popugame-replay__title" data-pg-replay-title>Loading replay…</h2>
			<div class="popugame-replay__meta" data-pg-replay-meta></div>
		</div>
		<div class="popugame__scorebox popugame-replay__scorebox" data-pg-replay-scorebox hidden aria-label="Score">
			<div class="popugame__nameline">
				<span class="popugame__name popugame__name--p0" data-pg-replay-name-p0>Player 1</span>
				<span class="popugame__name-sep">vs</span>
				<span class="popugame__name popugame__name--p1" data-pg-replay-name-p1>Player 2</span>
			</div>
			<div class="popugame__scoreline">
				<span class="popugame__score popugame__score--p0" data-pg-replay-score-p0>0</span>
				<span class="popugame__score-sep">:</span>
				<span class="popugame__score popugame__score--p1" data-pg-replay-score-p1>0</span>
			</div>
			<div class="popugame__score-label">claimed cells</div>
		</div>
		<div class="popugame__board popugame-replay__board" data-pg-replay-board aria-label="Replay board"></div>
		<div class="popugame-replay__controls">
			<button class="btn popugame__btn" id="pg-replay-first" type="button" title="First move">&#8676;</button>
			<button class="btn popugame__btn" id="pg-replay-prev" type="button">&#8592; Prev</button>
			<span class="popugame-replay__step" data-pg-replay-step>— / —</span>
			<button class="btn popugame__btn" id="pg-replay-next" type="button">Next &#8594;</button>
			<button class="btn popugame__btn" id="pg-replay-last" type="button" title="Last move">&#8677;</button>
			<button class="btn btn--ghost popugame__btn" id="pg-replay-autoplay" type="button">&#9654; Play</button>
		</div>
		<div class="popugame-replay__notice" data-pg-replay-notice hidden></div>
	</div>
	<div class="popugame-landing__back">
		<a href="/popugame" class="btn btn--ghost">&#8592; Back to PopuGame</a>
	</div>
	"""
	return _render(
		Page(
			title=f"PopuGame Replay — {code_safe}",
			children=(RawHtml(body_html),),
			stylesheets=(
				"/static/css/popugame.css",
			),
			scripts=(
				"/static/js/popugame_replay.js",
			),
		),
		ctx,
	)


def build_popugame_invalid_link_page(user: dict | None) -> str:
	ctx = _page_context(user)
	return _render(
		Page(
			title="Invalid PopuGame Link",
			children=(
				Heading("Invalid PopuGame Link", 2),
				Text("This PopuGame link is invalid or no longer available."),
				Text("Please host a new game or ask the host for a fresh link/code."),
				RawHtml(html_fragments.return_home()),
			),
		),
		ctx,
	)


def build_psql_interface_page(user: dict | None) -> str:
	ctx = _page_context(user)
	admin_error = _admin_guard(ctx, "Database Admin")
	if admin_error:
		return admin_error
	page_html = load_psql_interface_page_html(_get_interface())
	return _render(
		Page(
			title="Database Admin",
			children=(RawHtml(page_html),),
			stylesheets=("/static/css/forms.css", "/static/css/db_interface.css"),
			scripts=(
				"/static/js/form_submit.js",
				"/static/js/db_interface_resize.js",
				"/static/js/db_interface_actions.js",
				"/static/js/db_interface_userid.js",
			),
		),
		ctx,
	)


def build_admin_email_debug_page(user: dict | None) -> str:
	ctx = _page_context(user)
	admin_error = _admin_guard(ctx, "Debug Email")
	if admin_error:
		return admin_error
	page_html = html_fragments.email_debug_form() + html_fragments.email_debug_script()
	return _centered_raw_page(
		ctx,
		title="Debug Email",
		body_html=page_html,
		stylesheets=("/static/css/forms.css", "/static/css/centering.css"),
		scripts=("/static/js/form_submit.js",),
	)


def build_admin_dashboard_page(user: dict | None) -> str:
	ctx = _page_context(user)
	admin_error = _admin_guard(ctx, "Admin Dashboard")
	if admin_error:
		return admin_error
	return _render(
		Page(
			title="Admin Dashboard",
			children=(Stack(children=(RawHtml(load_admin_dashboard_page_html(_get_interface())),), data_attrs={"page": "admin-dashboard"}),),
			stylesheets=("/static/css/admin_dashboard.css",),
		),
		ctx,
	)


def build_admin_frontend_test_page(user: dict | None) -> str:
	ctx = _page_context(user)
	admin_error = _admin_guard(ctx, "Frontend Test")
	if admin_error:
		return admin_error
	return _render(
		Page(
			title="Frontend Test",
			children=(RawHtml(_build_frontend_test_page_html()),),
			stylesheets=(
				"/static/css/frontend_test.css",
				"/static/css/forms.css",
				"/static/css/profile.css",
				"/static/css/admin_dashboard.css",
				"/static/css/admin_users.css",
				"/static/css/minecraft.css",
				"/static/css/metrics_dashboard.css",
				"/static/css/login.css",
				"/static/css/centering.css",
				"/static/css/landing_nav.css",
			),
		),
		ctx,
	)


def build_admin_users_page(user: dict | None) -> str:
	ctx = _page_context(user)
	admin_error = _admin_guard(ctx, "User Management")
	if admin_error:
		return admin_error
	return _render(
		Page(
			title="User Management",
			children=(Stack(children=(RawHtml(load_admin_users_page_html(_get_interface())),), data_attrs={"page": "admin-users"}),),
			stylesheets=("/static/css/profile.css", "/static/css/admin_users.css"),
			scripts=("/static/js/admin_users.js", "/static/js/copy_tooltip.js"),
		),
		ctx,
	)


def _build_approvals_page(user: dict | None, kind: str) -> str:
	ctx = _page_context(user)
	model = load_admin_approvals_page_html(_get_interface(), kind)
	content_html = html_fragments.heading(model["title"], 1)
	if model["cards_html"]:
		content_html += model["cards_html"]
	else:
		content_html += html_fragments.paragraph("No pending requests.")
	return _render(
		Page(
			title=model["title"],
			children=(Stack(children=(RawHtml(content_html),), data_attrs={"page": "admin-approvals", "approval-kind": kind}),),
			stylesheets=("/static/css/forms.css", "/static/css/centering.css"),
			scripts=("/static/js/admin_approvals.js",),
		),
		ctx,
	)


def build_admin_audiobookshelf_approvals_page(user: dict | None) -> str:
	ctx = _page_context(user)
	admin_error = _admin_guard(ctx, "Audiobookshelf Approvals")
	if admin_error:
		return admin_error
	return _build_approvals_page(user, "audiobookshelf")


def build_admin_discord_webhook_approvals_page(user: dict | None) -> str:
	ctx = _page_context(user)
	admin_error = _admin_guard(ctx, "Discord Webhook Approvals")
	if admin_error:
		return admin_error
	return _build_approvals_page(user, "discord-webhook")


def build_admin_minecraft_approvals_page(user: dict | None) -> str:
	ctx = _page_context(user)
	admin_error = _admin_guard(ctx, "Minecraft Approvals")
	if admin_error:
		return admin_error
	return _build_approvals_page(user, "minecraft")


def build_admin_api_access_approvals_page(user: dict | None) -> str:
	ctx = _page_context(user)
	admin_error = _admin_guard(ctx, "API Access Approvals")
	if admin_error:
		return admin_error
	return _build_approvals_page(user, "api-access")


def build_integration_remove_page(user: dict | None, token: str | None = None) -> str:
	ctx = _page_context(user)
	resolved_token = token if token is not None else ctx.query_value("token")
	action = FormAction(
		route="/api/integration/remove",
		method="POST",
		success_redirect="/integration/removed",
	)
	form = Form(
		form_id="integration-remove-form",
		fields=(
			HiddenField("token", resolved_token),
			RawHtml("<div class=\"form-group\"><p>This integration was created without a linked account. Confirm removal below.</p></div>"),
		),
		submit_buttons=(
			_submit_button("Remove integration", action, class_name="danger"),
		),
		action=action,
	)
	form_rendered = form.render(ctx)
	body_html = (
		html_fragments.heading("Remove Integration", 2)
		+ form_rendered.html
	)
	return _render(
		Page(
			title="Remove Integration",
			children=(
				Stack(
					data_attrs={"page": "integration-remove"},
					children=(
						RawHtml(
							html_fragments.center_column(body_html),
							stylesheets=form_rendered.stylesheets,
							scripts=form_rendered.scripts,
						),
					),
				),
			),
			stylesheets=("/static/css/centering.css",),
		),
		ctx,
	)


def build_integration_removed_page(user: dict | None) -> str:
	ctx = _page_context(user)
	return _centered_raw_page(
		ctx,
		title="Integration Removed",
		page_id="integration-removed",
		body_html=(
			html_fragments.heading("Integration Removed", 2)
			+ html_fragments.paragraph("Your integration has been removed successfully.")
			+ html_fragments.return_home()
		),
		stylesheets=("/static/css/centering.css",),
	)


def build_audiobookshelf_unavailable_page(user: dict | None, status_note: str | None = None) -> str:
	ctx = _page_context(user)
	note = status_note or "The service did not respond."
	return _centered_raw_page(
		ctx,
		title="Audiobookshelf Offline",
		page_id="audiobookshelf-unavailable",
		body_html=(
			html_fragments.heading("Audiobookshelf is offline", 2)
			+ html_fragments.paragraph("We could not reach the Audiobookshelf service on this machine.")
			+ html_fragments.paragraph(f"Status: {html.escape(note)}")
			+ html_fragments.return_home()
		),
		stylesheets=("/static/css/centering.css",),
	)


def build_error_page(user: dict | None, e) -> str:
	ctx = _page_context(user)
	if not hasattr(e, "code") or not hasattr(e, "description"):
		e.code = 500
		e.description = "An unexpected error occurred."
	return _render(
		Page(
			title=f"{e.code} Error",
			children=(
				RawHtml(html_fragments.error_header(e.code, e.description)),
				RawHtml(html_fragments.return_home()),
			),
		),
		ctx,
	)


def build_501_page(user: dict | None = None) -> str:
	ctx = _page_context(user)
	return _render(
		Page(
			title="501 Not Implemented",
			children=(
				RawHtml(html_fragments.error_header(501, "Not Implemented")),
				Text("The requested functionality is not yet implemented on this server."),
				RawHtml(html_fragments.return_home()),
			),
		),
		ctx,
	)


__all__ = [
	"is_admin_user",
	"build_501_page",
	"build_admin_api_access_approvals_page",
	"build_admin_audiobookshelf_approvals_page",
	"build_admin_dashboard_page",
	"build_admin_discord_webhook_approvals_page",
	"build_admin_email_debug_page",
	"build_admin_frontend_test_page",
	"build_admin_minecraft_approvals_page",
	"build_admin_users_page",
	"build_api_access_application_page",
	"build_audiobookshelf_registration_page",
	"build_audiobookshelf_unavailable_page",
	"build_delete_account_page",
	"build_discord_webhook_registration_page",
	"build_discord_webhook_verify_page",
	"build_discord_webhook_verified_page",
	"build_empty_landing_page",
	"build_error_page",
	"build_integration_remove_page",
	"build_integration_removed_page",
	"build_login_page",
	"build_minecraft_page",
	"build_popugame_invalid_link_page",
	"build_popugame_landing_page",
	"build_popugame_page",
	"build_popugame_replay_page",
	"build_profile_page",
	"build_psql_interface_page",
	"build_readme_page",
	"build_register_page",
	"build_reset_password_page",
	"build_server_metrics_page",
	"build_stock_viewer_page",
	"build_test_page",
	"build_verify_email_page",
	"build_verify_email_token_page",
	"render_markdown",
]
