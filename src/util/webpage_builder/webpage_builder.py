from __future__ import annotations

from dataclasses import dataclass
import base64
import json
import os
import flask
import html
import re
from pathlib import Path
from typing import Callable

from sql.psql_interface import PSQLInterface

from util.webpage_builder import parent_builder
from util.webpage_builder.parent_builder import HTMLHelper
from util.webpage_builder import html_fragments
from util.webpage_builder.metrics_builder import METRICS_NAMES
from util.integrations.discord.webhook_interface import DiscordWebhookEmitter
from util.fcr.file_config_reader import FileConfigReader

fcr = FileConfigReader()
interface = PSQLInterface()

class PageBuilder:
	def __init__(
		self,
		*,
		page_config: str = "default",
		navbar_config: str = "navbar_landing.json",
		user: dict | None = None,
	):
		self._b = parent_builder.WebPageBuilder()
		self._b.load_page_config(page_config)
		self._b._build_nav_html(navbar_config, user=user)

	def add_banner(
		self,
		lines: list[str],
		*,
		banner_type: str = "ticker",
		interval: int = 4000,
	):
		self._b._add_banner_html(lines, banner_type=banner_type, interval=interval)

	def add_metric_graph_grid(self, metric_names: list[str]):
		self._b._add_plotly_metric_graph_grid(metric_names)

	def add_html(self, html: str):
		self._b._add_main_content_html(html)

	def add_login_window(self):
		self._b._add_login_window()

	def add_register_window(self):
		self._b._add_register_window()

	def render(self) -> str:
		return self._b.serve_html()
	
def step_box(
	*,
	class_name: str = "login-window",
	container_class: str = "login-container",
	contents: tuple[Step, ...] = (),
) -> Step:
	def _step(builder: PageBuilder):
		builder._b.stylesheets.add("/static/css/login.css")
		builder.add_html(html_fragments.box_open(container_class, class_name))
		for s in contents:
			s(builder)
		builder.add_html(html_fragments.box_close())
	return _step

HARDWARE_BANNER_LINES = [
	"New website just dropped!",
	"Using new server hardware as well.",
	"Server hardware: ODroid-H4 Ultra",
	"Core™ i3 Processor N305",
	"32 GB Ram",
	"2TB NVMe SSD",
	"2x 8TB HDD",
]

LOREM_IPSUM = fcr.find("lorem_ipsum.txt")

def add_return_home(builder: PageBuilder):
	builder.add_html(html_fragments.return_home())


Step = Callable[[PageBuilder], None]

def step_group(*steps: Step) -> Step:
	def _step(builder: PageBuilder):
		for s in steps:
			s(builder)
	return _step

def step_wrap(open_html: str, close_html: str, *, contents: tuple[Step, ...]) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(open_html)
		for s in contents:
			s(builder)
		builder.add_html(close_html)
	return _step

@dataclass(frozen=True)
class PageSpec:
	page_config: str = "default"
	navbar_config: str = "navbar_landing.json"
	steps: tuple[Step, ...] = ()


def build_page(user: dict | None, spec: PageSpec) -> str:
	navbar_config = spec.navbar_config
	if user and navbar_config == "navbar_landing.json":
		try:
			if interface.is_admin(user.get("id")):
				navbar_config = "navbar_landing_admin.json"
		except Exception:
			navbar_config = spec.navbar_config
	builder = PageBuilder(page_config=spec.page_config, navbar_config=navbar_config, user=user)
	for step in spec.steps:
		step(builder)
	return builder.render()

def step_hardware_banner(builder: PageBuilder):
	builder.add_banner(HARDWARE_BANNER_LINES, banner_type="ticker", interval=4000)


def step_metrics_grid(builder: PageBuilder):
	builder.add_metric_graph_grid(list(METRICS_NAMES.keys()))


def step_lorem_ipsum(n: int = 1) -> Step:
	def _step(builder: PageBuilder):
		lorem_paragraphs = LOREM_IPSUM.split("\n\n")
		for i in range(n):
			paragraph = lorem_paragraphs[i % len(lorem_paragraphs)]
			builder.add_html(html_fragments.paragraph(paragraph))
	return _step


def step_error_header(code: int, description: str) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(html_fragments.error_header(code, description))
	return _step


def step_text_block(html: str) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(html)
	return _step


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_CODE_RE = re.compile(r"`([^`]+)`")
_MD_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")


def _md_inline(text: str) -> str:
	escaped = html.escape(text)
	escaped = _MD_LINK_RE.sub(lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', escaped)
	escaped = _MD_CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", escaped)
	escaped = _MD_BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", escaped)
	escaped = _MD_ITALIC_RE.sub(lambda m: f"<em>{m.group(1)}</em>", escaped)
	return escaped


def render_markdown(md_text: str) -> str:
	text = (md_text or "").replace("\r\n", "\n").replace("\r", "\n")
	lines = text.split("\n")
	blocks: list[str] = []
	paragraph: list[str] = []

	def flush_paragraph():
		if not paragraph:
			return
		body = " ".join(s for s in paragraph if s).strip()
		if body:
			blocks.append(f"<p>{_md_inline(body)}</p>")
		paragraph.clear()

	i = 0
	while i < len(lines):
		line = lines[i]
		if not line.strip():
			flush_paragraph()
			i += 1
			continue

		if line.startswith("```"):
			flush_paragraph()
			lang = line[3:].strip()
			code_lines: list[str] = []
			i += 1
			while i < len(lines) and not lines[i].startswith("```"):
				code_lines.append(lines[i])
				i += 1
			if i < len(lines) and lines[i].startswith("```"):
				i += 1
			code_html = html.escape("\n".join(code_lines))
			class_attr = f' class="language-{html.escape(lang)}"' if lang else ""
			blocks.append(f"<pre><code{class_attr}>{code_html}</code></pre>")
			continue

		if line.lstrip().startswith(">"):
			flush_paragraph()
			quote_lines: list[str] = []
			while i < len(lines) and lines[i].lstrip().startswith(">"):
				qline = lines[i].lstrip()[1:]
				if qline.startswith(" "):
					qline = qline[1:]
				quote_lines.append(qline)
				i += 1
			quote_text = "\n".join(quote_lines).strip()
			if quote_text:
				blocks.append(f"<blockquote>{_md_inline(quote_text).replace(chr(10), '<br>')}</blockquote>")
			continue

		heading = re.match(r"^(#{1,6})\s+(.*)$", line)
		if heading:
			flush_paragraph()
			level = len(heading.group(1))
			body = _md_inline(heading.group(2).strip())
			blocks.append(f"<h{level}>{body}</h{level}>")
			i += 1
			continue

		if re.match(r"^(-|\*|\+)\s+.+", line):
			flush_paragraph()
			items: list[str] = []
			while i < len(lines) and re.match(r"^(-|\*|\+)\s+.+", lines[i]):
				item = re.sub(r"^(-|\*|\+)\s+", "", lines[i], count=1).strip()
				items.append(f"<li>{_md_inline(item)}</li>")
				i += 1
			blocks.append("<ul>" + "".join(items) + "</ul>")
			continue

		if re.match(r"^\d+\.\s+.+", line):
			flush_paragraph()
			items = []
			while i < len(lines) and re.match(r"^\d+\.\s+.+", lines[i]):
				item = re.sub(r"^\d+\.\s+", "", lines[i], count=1).strip()
				items.append(f"<li>{_md_inline(item)}</li>")
				i += 1
			blocks.append("<ol>" + "".join(items) + "</ol>")
			continue

		if re.match(r"^(-{3,}|\*{3,}|_{3,})$", line.strip()):
			flush_paragraph()
			blocks.append("<hr>")
			i += 1
			continue

		paragraph.append(line.strip())
		i += 1

	flush_paragraph()
	return "\n".join(blocks)


def step_markdown_block(md_text: str, *, class_name: str = "markdown-block") -> Step:
	def _step(builder: PageBuilder):
		builder._b.stylesheets.add("/static/css/markdown.css")
		builder.add_html(f'<div class="{class_name}">{render_markdown(md_text)}</div>')
	return _step

def step_text_paragraph(text: str) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(html_fragments.paragraph(text))
	return _step

def step_heading(text: str, level: int = 2) -> Step:
	def _step(builder: PageBuilder):
		lvl = min(max(int(level), 1), 6)
		builder.add_html(html_fragments.heading(text, lvl))
	return _step


def step_link_paragraph(text: str, href: str) -> Step:
	def _step(builder: PageBuilder):
		link = HTMLHelper.link_string(text=text, href=href)
		builder.add_html(html_fragments.link_paragraph(link))
	return _step

def step_set_page_title(title: str) -> Step:
	def _step(builder: PageBuilder):
		builder._b.set_page_title(title)
	return _step

def step_form(
	*,
	form_id: str = "",
	class_name: str = "form",
	contents: tuple[Step, ...] = (),
) -> Step:
	def _step(builder: PageBuilder):
		builder._b.stylesheets.add("/static/css/forms.css")
		builder._b.scripts.add("/static/js/form_submit.js")

		builder.add_html(html_fragments.form_open(form_id=form_id, class_name=class_name))
		for s in contents:
			s(builder)
		builder.add_html(html_fragments.form_close())
	return _step



def step_form_group(inner_html: str, class_name: str = "form-group") -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(HTMLHelper.form_group(inner_html, class_name=class_name))
	return _step


def step_text_input_group(
	label: str,
	name: str,
	placeholder: str = "",
	value: str = "",
	class_name: str = "",
	prefill: str | None = None,
	group_class: str = "form-group",
) -> Step:
	def _step(builder: PageBuilder):
		input_html = HTMLHelper.text_input(
			label=label,
			name=name,
			placeholder=placeholder,
			value=value,
			class_name=class_name,
			prefill=prefill,
		)
		builder.add_html(HTMLHelper.form_group(input_html, class_name=group_class))
	return _step

def step_textarea_group(
	label: str,
	name: str,
	placeholder: str = "",
	value: str = "",
	rows: int = 8,
	class_name: str = "",
	group_class: str = "form-group",
) -> Step:
	def _step(builder: PageBuilder):
		textarea_html = HTMLHelper.textarea_input(
			label=label,
			name=name,
			placeholder=placeholder,
			value=value,
			class_name=class_name,
			rows=rows,
		)
		builder.add_html(
			HTMLHelper.form_group(textarea_html, class_name=group_class)
		)
	return _step

def step_password_input_group(
	label: str,
	name: str,
	placeholder: str = "",
	value: str = "",
	class_name: str = "",
	prefill: str | None = None,
	hide_value: bool = True,
	group_class: str = "form-group",
) -> Step:
	def _step(builder: PageBuilder):
		input_html = HTMLHelper.password_input(
			label=label,
			name=name,
			placeholder=placeholder,
			value=value,
			class_name=class_name,
			prefill=prefill,
			hide_value=hide_value,
		)
		builder.add_html(HTMLHelper.form_group(input_html, class_name=group_class))
	return _step


def step_checkbox_group(
	label: str,
	name: str,
	checked: bool = False,
	class_name: str = "",
	group_class: str = "form-group",
) -> Step:
	def _step(builder: PageBuilder):
		box_html = HTMLHelper.checkbox_input(
			label=label,
			name=name,
			checked=checked,
			class_name=class_name,
		)
		builder.add_html(HTMLHelper.form_group(box_html, class_name=group_class))
	return _step


def step_hidden_input(name: str, value: str) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(HTMLHelper.hidden_input(name=name, value=value))
	return _step


def step_submit_button(
	text: str,
	*,
	submission_fields: list[str] | None = None,
	submission_route: str = "",
	submission_method: str = "POST",
	success_redirect: str = "",
	failure_redirect: str = "",
	class_name: str = "primary",
	group_class: str = "form-group",
) -> Step:
	def _step(builder: PageBuilder):
		btn_html = HTMLHelper.submit_button(
			text=text,
			submission_fields=submission_fields,
			submission_route=submission_route,
			submission_method=submission_method,
			success_redirect=success_redirect,
			failure_redirect=failure_redirect,
		)

		# Add a class to the button by simple injection if desired
		# (keeps HTMLHelper.submit_button minimal)
		btn_html = html_fragments.add_button_class(btn_html, class_name)

		builder.add_html(HTMLHelper.form_group(btn_html, class_name=group_class))
	return _step


def step_form_message_area(
	attr: str = "data-form-message",
	class_name: str = "form-message",
	lines: int = 2,
) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(html_fragments.form_message_area(class_name, attr, lines))
	return _step

def step_dropdown_group(
	label: str,
	name: str,
	options: list[tuple[str, str]],
	selected: str = "",
	placeholder: str | None = None,
	class_name: str = "",
	required: bool = False,
	group_class: str = "form-group",
) -> Step:
	def _step(builder: PageBuilder):
		html = HTMLHelper.dropdown(
			label=label,
			name=name,
			options=options,
			selected=selected,
			placeholder=placeholder,
			class_name=class_name,
			required=required,
		)
		builder.add_html(HTMLHelper.form_group(html, class_name=group_class))
	return _step

def step_centering(
	*,
	class_name: str = "centering-container",
	max_width: str = "1024px",
	padding_y: str = "0.75rem",
	padding_x: str = "0.5rem",
	contents: tuple[Step, ...] = (),
) -> Step:
	def _step(builder: PageBuilder):
		builder._b.stylesheets.add("/static/css/centering.css")
		builder.add_html(html_fragments.centering_open(
			class_name=class_name,
			max_width=max_width,
			padding_y=padding_y,
			padding_x=padding_x,
		))
		for s in contents:
			s(builder)
		builder.add_html(html_fragments.centering_close())
	return _step


def step_centered_box(
	*,
	href: str | None = None,
	class_name: str = "centered-box",
	rounding: str = "1rem",
	padding_y: str = "0.25rem",
	padding_x: str = "1.1rem",
	contents: tuple[Step, ...] = (),
) -> Step:
	def _step(builder: PageBuilder):
		builder._b.stylesheets.add("/static/css/centering.css")
		builder.add_html(html_fragments.centered_box_open(
			class_name=class_name,
			rounding=rounding,
			padding_y=padding_y,
			padding_x=padding_x,
			href=href,
		))
		for s in contents:
			s(builder)
		builder.add_html(html_fragments.centered_box_close(is_link=bool(href)))
	return _step


# ----------------------------
# Page builders
# ----------------------------

def build_test_page(user: dict | None) -> str:
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Test Page"),
			step_hardware_banner,
			step_metrics_grid,
			step_lorem_ipsum(2),
		),
	))

def build_readme_page(user: dict | None) -> str:
	readme_path = Path(__file__).resolve().parents[3] / "README.md"
	try:
		readme_text = readme_path.read_text(encoding="utf-8", errors="replace")
	except Exception as exc:
		readme_text = f"Failed to load README.md: {exc}"

	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("README"),
			step_heading("README", 1),
			step_centering(
				max_width="900px",
				contents=(step_markdown_block(readme_text),),
			),
		),
	))

def build_profile_page(user: dict | None) -> str:
	user_name = f"{user['first_name']} {user['last_name']}"
	navbar_config = "navbar_landing.json"
	is_admin = False
	if interface.is_admin(user.get("id")):
		navbar_config = "navbar_landing_admin.json"
		is_admin = True
	admin_since = None
	if is_admin:
		try:
			rows, _ = interface.client.get_rows_with_filters(
				"admins",
				equalities={"user_id": user.get("id")},
				page_limit=1,
				page_num=0,
			)
			if rows:
				admin_since = rows[0].get("created_at")
		except Exception:
			admin_since = None
	builder = PageBuilder(navbar_config=navbar_config, user=user)
	builder._b.stylesheets.add("/static/css/profile.css")
	builder._b.scripts.add("/static/js/profile_integrations.js")
	admin_line = ""
	if admin_since:
		admin_line = html_fragments.profile_admin_line(admin_since.strftime("%d %B %Y"))
	integration_cards: list[tuple[int, str]] = []
	try:
		webhooks, _ = interface.client.get_rows_with_filters(
			"discord_webhooks",
			equalities={"user_id": user.get("id")},
			page_limit=50,
			page_num=0,
		)
		for wh in webhooks or []:
			subscriptions_html = ""
			try:
				sub_rows = interface.client.execute_query(
					"SELECT s.id, s.event_key, s.is_active, s.created_at, "
					"ek.permission, ek.description "
					"FROM discord_webhook_subscriptions s "
					"LEFT JOIN discord_event_keys ek ON ek.event_key = s.event_key "
					"WHERE s.webhook_id = %s "
					"ORDER BY "
					"CASE COALESCE(ek.permission, '') "
					"WHEN 'admins' THEN 1 "
					"WHEN 'users' THEN 2 "
					"WHEN 'all' THEN 3 "
					"ELSE 4 END, "
					"s.created_at DESC;",
					(wh.get("id"),),
				) or []
				if sub_rows:
					sub_cards = []
					for sub in sub_rows:
						perm = (sub.get("permission") or "unknown").upper()
						desc = sub.get("description") or ""
						created = sub.get("created_at")
						date_str = ""
						if created:
							try:
								date_str = created.strftime("%d %B %Y")
							except Exception:
								date_str = str(created)
						is_active = bool(sub.get("is_active", True))
						status_label = "Active" if is_active else "Inactive"
						unsubscribe_html = ""
						if is_active:
							unsubscribe_html = html_fragments.subscription_action(
								"unsubscribe",
								str(sub.get("id")),
								"/api/profile/discord-webhook/unsubscribe",
								"Unsubscribe",
							)
						resubscribe_html = ""
						if not is_active:
							resubscribe_html = html_fragments.subscription_action(
								"resubscribe",
								str(sub.get("id")),
								"/api/profile/discord-webhook/resubscribe",
								"Resubscribe",
							)
						sub_cards.append(
							html_fragments.subscription_card(
								sub.get("event_key") or "",
								perm,
								desc,
								date_str,
								status_label,
								is_active,
								unsubscribe_html,
								resubscribe_html,
							)
						)
					subscriptions_html = html_fragments.integration_subscriptions(
						"Subscriptions",
						"".join(sub_cards),
					)
				else:
					subscriptions_html = html_fragments.integration_subscriptions_empty("Subscriptions")
			except Exception:
				subscriptions_html = ""

			status = "Active" if wh.get("is_active", True) else "Suspended"
			delete_button = ""
			badge_html = html_fragments.integration_badge(status)
			if status == "Active":
				delete_button = html_fragments.integration_delete_action(
					"discord_webhook",
					str(wh.get("id")),
					"Discord Webhook",
					True,
				)
			integration_cards.append((
				0 if status == "Active" else 1,
				html_fragments.integration_card(
					"discord_webhook",
					str(wh.get("id")),
					"Discord Webhook",
					html.escape(wh.get("name") or "Webhook"),
					html.escape(wh.get("webhook_url") or ""),
					badge_html + delete_button,
					subscriptions_html,
				)
			))
	except Exception:
		pass

	try:
		whitelist_rows, _ = interface.client.get_rows_with_filters(
			"minecraft_whitelist",
			equalities={"user_id": user.get("id")},
			page_limit=5,
			page_num=0,
		)
		for row in whitelist_rows or []:
			joined = ""
			if row.get("whitelisted_at"):
				try:
					joined = row["whitelisted_at"].strftime("%d %B %Y")
				except Exception:
					joined = str(row["whitelisted_at"])
			status = "Whitelisted" if row.get("is_active", True) else "Suspended"
			delete_button = ""
			badge_html = html_fragments.integration_badge(status)
			if status == "Whitelisted":
				delete_button = html_fragments.integration_delete_action(
					"minecraft",
					str(row.get("id")),
					"Minecraft",
					True,
				)
			integration_cards.append((
				0 if status == "Whitelisted" else 1,
				html_fragments.integration_card(
					"minecraft",
					str(row.get("id")),
					"Minecraft",
					f"Username: {html.escape(row.get('mc_username') or '')}",
					f"Whitelisted {html.escape(joined) if joined else ''}",
					badge_html + delete_button,
				)
			))
	except Exception:
		pass

	try:
		abs_rows, _ = interface.client.get_rows_with_filters(
			"audiobookshelf_registrations",
			equalities={"user_id": user.get("id"), "status": "approved"},
			page_limit=1,
			page_num=0,
		)
		if abs_rows:
			row = abs_rows[0]
			approved_at = ""
			if row.get("reviewed_at"):
				try:
					approved_at = row["reviewed_at"].strftime("%d %B %Y")
				except Exception:
					approved_at = str(row["reviewed_at"])
			status = "Approved" if row.get("is_active", True) else "Suspended"
			delete_button = ""
			badge_html = html_fragments.integration_badge(status)
			if status == "Approved":
				delete_button = html_fragments.integration_delete_action(
					"audiobookshelf",
					str(row.get("id")),
					"Audiobookshelf",
					True,
				)
			integration_cards.append((
				0 if status == "Approved" else 1,
				html_fragments.integration_card(
					"audiobookshelf",
					str(row.get("id")),
					"Audiobookshelf",
					html.escape(row.get("email") or user.get("email") or ""),
					f"Approved {html.escape(approved_at) if approved_at else ''}",
					badge_html + delete_button,
				)
			))
	except Exception:
		pass

	if not integration_cards:
		integration_cards.append(
			(0,
				html_fragments.integration_card_empty()
			)
		)
	integration_cards.sort(key=lambda item: item[0])
	integration_cards_html = "".join(card for _, card in integration_cards)
	profile_panels = html_fragments.profile_password_panel() + html_fragments.profile_delete_panel()
	profile_card_html = html_fragments.profile_card(
		initials=user["first_name"][:1] + user["last_name"][:1],
		user_name=user_name,
		created_at=user["created_at"].strftime("%d %B %Y"),
		email=user["email"],
		badge_label="ADMIN" if is_admin else "MEMBER",
		admin_line=admin_line,
		panels_html=profile_panels,
	)
	integrations_html = html_fragments.profile_integrations_header(
		"Linked integrations",
		"Services connected to your account.",
		integration_cards_html,
	)
	modal_html = html_fragments.integration_delete_modal(
		html_fragments.integration_delete_reason_select(
			[
				("", "Select a reason"),
				("no-longer-needed", "No longer needed"),
				("privacy", "Privacy/security concerns"),
				("incorrect", "Incorrect setup"),
				("switching", "Switching accounts"),
				("other", "Other"),
			]
		)
	)
	builder.add_html(
		html_fragments.profile_page_shell(
			profile_card_html + integrations_html + modal_html
		)
	)
	builder._b.set_page_title(user_name + "'s Profile")
	return builder.render()


def build_login_page(user: dict | None) -> str:
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Login"),
			step_box(contents=(
				step_heading("Login", 2),

				step_form(
					form_id="login-form",
					class_name="form",
					contents=(
						step_text_input_group("Email", "email", placeholder="Email"),
						step_password_input_group("Password", "password", placeholder="Password"),
						step_checkbox_group("Remember me", "remember_me"),
						step_submit_button(
							"Log in",
							submission_fields=["email", "password", "remember_me"],
							submission_route="/login",
							submission_method="POST",
							success_redirect="profile",
							failure_redirect=None,
						),
						step_form_message_area(),
					),
				),
				step_link_paragraph("Register new account", "/register"),
				step_link_paragraph("Forgot password", "/forgot-password"),
			)),
		),
	))


def build_register_page(user: dict | None) -> str:
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Register"),
			step_box(contents=(
				step_heading("Register", 2),
				step_form(
					form_id="register-form",
					class_name="form",
					contents=(
						step_dropdown_group(
							label="How did you discover my website?",
							name="referral_source",
							options=[
								("friend", "Friend or colleague"),
								("github", "GitHub"),
								("resume", "Resume / CV"),
								("linkedin", "LinkedIn or online profile"),
								("other", "Other"),
							],
						),
						step_text_input_group("First Name", "first_name", placeholder="First Name"),
						step_text_input_group("Last Name", "last_name", placeholder="Last Name"),
						step_text_input_group("Email", "email", placeholder="Email"),
						step_password_input_group("Password", "password", placeholder="Password"),
						step_password_input_group("Repeat Password", "repeat_password", placeholder="Repeat password"),
						step_submit_button(
							"Create account",
							submission_fields=["referral_source", "first_name", "last_name", "email", "password", "repeat_password"],
							submission_route="/register",
							submission_method="POST",
							success_redirect="verify-email",
							failure_redirect=None,
						),
						step_form_message_area(),
					),
				),
				step_link_paragraph("Login to existing account", "/login"),
			)),
		),
	))


def build_audiobookshelf_registration_page(user: dict | None) -> str:
	return build_page(user, PageSpec(
		steps=_build_audiobookshelf_registration_steps(user),
	))


def _build_audiobookshelf_registration_steps(user: dict | None) -> tuple[Step, ...]:
	contact_fields: tuple[Step, ...] = ()
	hidden_contact: tuple[Step, ...] = ()
	submission_fields = ["first_name", "last_name", "email", "additional_info"]
	if user:
		hidden_contact = (
			step_text_block(HTMLHelper.hidden_input("first_name", user.get("first_name", ""))),
			step_text_block(HTMLHelper.hidden_input("last_name", user.get("last_name", ""))),
			step_text_block(HTMLHelper.hidden_input("email", user.get("email", ""))),
		)
	else:
		contact_fields = (
			step_text_input_group("First Name", "first_name", placeholder="First Name"),
			step_text_input_group("Last Name", "last_name", placeholder="Last Name"),
			step_text_input_group("Email", "email", placeholder="Email"),
		)

	return (
		step_set_page_title("Audiobookshelf Registration"),
		step_box(contents=(
			step_heading("Audiobookshelf Registration", 2),
			step_form(
				form_id="audiobookshelf-registration-form",
				class_name="form",
				contents=(
					*contact_fields,
					*hidden_contact,
					step_textarea_group("Additional Information", "additional_info", placeholder="Enter any additional information here..."),
					step_submit_button(
						"Submit Registration",
						submission_fields=submission_fields,
						submission_route="/audiobookshelf-registration",
						submission_method="POST",
						success_redirect="/",
						failure_redirect=None,
					),
					step_form_message_area(),
				),
			),
			step_text_paragraph("You will receive a follow-up email with further instructions if your registration is approved."),
		)),
	)

def build_discord_webhook_registration_page(user: dict | None) -> str:
	return build_page(user, PageSpec(
		steps=_build_discord_webhook_registration_steps(user),
	))


def _build_discord_webhook_registration_steps(user: dict | None) -> tuple[Step, ...]:
	allowed_permissions = ["all"]
	if user:
		allowed_permissions.append("users")
	if user and interface.is_admin(user.get("id")):
		allowed_permissions.append("admins")

	event_options = []
	try:
		rows, _ = interface.client.get_rows_with_filters(
			"discord_event_keys",
			raw_conditions=["permission = ANY(%s)"],
			raw_params=[allowed_permissions],
			page_limit=1000,
			page_num=0,
			order_by="event_key",
			order_dir="ASC",
		)
		for r in rows:
			label = r["event_key"]
			desc = r.get("description")
			if desc:
				label = f"{label} — {desc}"
			event_options.append((r["event_key"], label))
	except Exception:
		event_options = []

	contact_fields: tuple[Step, ...] = ()
	verify_fields = ["name", "webhook_url", "event_key"]
	if not user:
		contact_fields = (
			step_text_input_group("First Name", "first_name", placeholder="First Name"),
			step_text_input_group("Last Name", "last_name", placeholder="Last Name"),
			step_text_input_group("Contact Email", "contact_email", placeholder="you@example.com"),
		)
		verify_fields.extend(["first_name", "last_name", "contact_email"])

	webhook_inputs: tuple[Step, ...] = (
		step_text_input_group("Webhook Name", "name", placeholder="My Webhook"),
		step_text_input_group("Webhook URL", "webhook_url", placeholder="https://discord.com/api/webhooks/..."),
	)
	webhook_extras: tuple[Step, ...] = ()
	if user:
		try:
			webhook_rows, _ = interface.client.get_rows_with_filters(
				"discord_webhooks",
				equalities={"user_id": user.get("id")},
				page_limit=50,
				page_num=0,
			)
			webhook_pairs = []
			for row in webhook_rows or []:
				name = (row.get("name") or "").strip()
				url = (row.get("webhook_url") or "").strip()
				if not name or not url:
					continue
				webhook_pairs.append({"name": name, "url": url})
			if webhook_pairs:
				options_json = json.dumps(webhook_pairs)
				options_b64 = base64.b64encode(options_json.encode("utf-8")).decode("ascii")
				name_input_html = html_fragments.webhook_selector_input(
					label="Webhook Name",
					input_id="name",
					name="name",
					placeholder="My Webhook",
					data_kind="name",
				)
				url_input_html = html_fragments.webhook_selector_input(
					label="Webhook URL",
					input_id="webhook_url",
					name="webhook_url",
					placeholder="https://discord.com/api/webhooks/...",
					data_kind="url",
				)
				webhook_inputs = (
					step_text_block(HTMLHelper.form_group(name_input_html)),
					step_text_block(HTMLHelper.form_group(url_input_html)),
				)
				def _add_script(builder_obj: PageBuilder):
					builder_obj._b.scripts.add("/static/js/webhook_selector.js")
				webhook_extras = (
					step_text_block(html_fragments.webhook_options_data_script(options_b64)),
					_add_script,
				)
		except Exception:
			webhook_inputs = (
				step_text_input_group("Webhook Name", "name", placeholder="My Webhook"),
				step_text_input_group("Webhook URL", "webhook_url", placeholder="https://discord.com/api/webhooks/..."),
			)
			webhook_extras = ()

	return (
		step_set_page_title("Discord Webhook Registration"),
		step_box(contents=(
			step_heading("Discord Webhook Registration", 2),
			step_form(
				form_id="discord-webhook-registration-form",
				class_name="form",
				contents=(
					*webhook_inputs,
					*webhook_extras,
					*contact_fields,
					step_dropdown_group(
						label="Event Key",
						name="event_key",
						options=event_options,
					),
					step_text_paragraph("Click to send a verification code to your webhook."),
					step_submit_button(
						"Send Verification Code",
						submission_fields=verify_fields,
						submission_route="/discord-webhook/verify",
						submission_method="POST",
						success_redirect="",
						failure_redirect=None,
					),
					step_form_message_area(),
				),
			),
		)),
	)

def build_discord_webhook_verify_page(user: dict | None) -> str:
	vid = flask.request.args.get("vid", "")
	code = flask.request.args.get("code", "")
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Webhook Verification"),
			step_box(contents=(
				step_heading("Enter Verification Code", 2),
				step_form(
					form_id="discord-webhook-verify-form",
					class_name="form",
					contents=(
						step_text_input_group("Verification Code", "verification_code", placeholder="6-digit code", value=code),
						step_text_block(HTMLHelper.hidden_input("verification_id", vid)),
						step_submit_button(
							"Submit Code",
							submission_fields=["verification_code", "verification_id"],
							submission_route="/discord-webhook/verify/submit",
							submission_method="POST",
							success_redirect="/discord-webhook/verified",
							failure_redirect=None,
						),
						step_form_message_area(),
					),
				),
				step_text_paragraph("If you opened this page from the webhook link, the ID is prefilled."),
				step_text_block(html_fragments.webhook_verify_autosubmit_script()),
			)),
		)
	))

def build_discord_webhook_verified_page(user: dict | None) -> str:
	status = (flask.request.args.get("status") or "").strip().lower()
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
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title(title),
			step_box(contents=(
				step_heading(title, 2),
				step_text_block(message),
				add_return_home,
			)),
		)
	))

def build_verify_email_page(user: dict | None) -> str:
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Verify Your Email"),
			step_box(contents=(
				step_heading("Verify Your Email", 2),
				step_text_paragraph("Thank you for registering! Please check your email for a verification link to complete your registration."),
			)),
		)
	))

def build_verify_email_token_page(user: dict | None, token: str) -> str:
	pending_user = None
	try:
		token_hash = interface._hash_verification_token(token)
		pending_rows = interface.get_pending_user({"verification_token_hash": token_hash})
		if pending_rows:
			pending_user = pending_rows[0]
	except Exception:
		pending_user = None

	validation, validation_message = interface.validate_verification_token(token)
	if validation:
		if pending_user:
			try:
				emitter = DiscordWebhookEmitter(interface)
				emitter.emit_event(
					"moderator.notifications",
					payload={
						"embeds": [
							{
								"title": "New account created",
								"fields": [
									{"name": "Action", "value": "account_created", "inline": True},
									{"name": "Subject", "value": pending_user.get("email", ""), "inline": True},
									{
										"name": "Details",
										"value": (
											f"- Name: {pending_user.get('first_name', '')} {pending_user.get('last_name', '')}\n"
											f"- Email: {pending_user.get('email', '')}\n"
											f"- User ID: {pending_user.get('id', '')}"
										),
										"inline": False,
									},
								],
							}
						]
					},
					context={
						"action": "account_created",
						"user_id": pending_user.get("id"),
						"email": pending_user.get("email"),
					},
				)
			except Exception:
				pass
	if validation:
		page_title = "Email Verified"
		message = html_fragments.paragraph("Your email has been successfully verified! You can now log in to your account.")
	else:
		page_title = "Verification Failed"
		message = (
			html_fragments.paragraph("The verification link could not be processed.")
			+ html_fragments.paragraph_with_strong("Reason:", html.escape(validation_message))
		)
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title(page_title),
			step_box(contents=(
				step_heading(page_title, 2),
				step_text_block(message),
			)),
		)
	))

def build_server_metrics_page(user: dict | None) -> str:
	builder = PageBuilder(user=user)
	builder._b.stylesheets.add("/static/css/metrics_dashboard.css")

	builder.add_html(html_fragments.metrics_dashboard_open())

	for key, label in METRICS_NAMES.items():
		builder.add_html(html_fragments.metrics_kpi_card(key, label))

	builder.add_html(html_fragments.metrics_dashboard_between_sections())

	builder.add_metric_graph_grid(list(METRICS_NAMES.keys()))

	builder.add_html(html_fragments.metrics_dashboard_close())
	builder._b.set_page_title("Server Metrics")
	return builder.render()

def build_reset_password_page(user: dict | None) -> str:
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Reset Password"),
			step_box(contents=(
				step_heading("Reset Password", 2),
				step_form(
					form_id="reset-password-form",
					class_name="form",
					contents=(
						step_text_input_group("Email", "email", placeholder="Email"),
						step_submit_button(
							"Send Reset Link",
							submission_fields=["email"],
							submission_route="/reset-password",
							submission_method="POST",
							success_redirect="/",
							failure_redirect=None,
						),
						step_form_message_area(),
					),
				),
			)),
		),
	))

def build_delete_account_page(user: dict | None) -> str:
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Delete Account"),
			step_box(contents=(
				step_heading("Delete Account", 2),
				step_text_paragraph("Deleting your account is irreversible."),
				step_form(
					form_id="delete-account-form",
					class_name="form",
					contents=(
						step_text_block(
							html_fragments.paragraph_with_bold(
								"Please enter your password to confirm deletion of the account for ",
								user["email"],
								":",
							)
						),
						step_password_input_group("Confirm Password", "password", placeholder="Password"),
						step_submit_button(
							"Delete Account",
							submission_fields=["password"],
							submission_route="/delete-account",
							submission_method="POST",
							success_redirect="/",
							failure_redirect=None,
							class_name="danger",
						),
						step_form_message_area(),
					),
				),
			)),
		),
	))

def build_minecraft_page(user: dict | None) -> str:
	contact_fields: tuple[Step, ...] = ()
	hidden_contact: tuple[Step, ...] = ()
	submission_fields = ["first_name", "last_name", "email", "mc_username", "who_are_you", "additional_info"]
	is_whitelisted = False
	whitelist_username = ""
	if user:
		hidden_contact = (
			step_text_block(HTMLHelper.hidden_input("first_name", user.get("first_name", ""))),
			step_text_block(HTMLHelper.hidden_input("last_name", user.get("last_name", ""))),
			step_text_block(HTMLHelper.hidden_input("email", user.get("email", ""))),
		)
		try:
			rows = interface.client.execute_query(
				"SELECT mc_username FROM minecraft_whitelist WHERE user_id = %s AND is_active = TRUE;",
				(user.get("id"),),
			) or []
			if rows:
				is_whitelisted = True
				whitelist_username = rows[0].get("mc_username") or ""
		except Exception:
			is_whitelisted = False
	else:
		contact_fields = (
			step_text_input_group("First Name", "first_name", placeholder="First Name"),
			step_text_input_group("Last Name", "last_name", placeholder="Last Name"),
			step_text_input_group("Email", "email", placeholder="Email"),
		)

	def add_minecraft_assets(builder: PageBuilder):
		builder._b.stylesheets.add("/static/css/minecraft.css")
		builder._b.scripts.add("/static/js/minecraft_status.js")

	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Minecraft Server"),
			add_minecraft_assets,
			step_text_block(html_fragments.minecraft_status_card()),
			step_text_block(html_fragments.minecraft_whitelist_banner(is_whitelisted, whitelist_username or "")),
			step_wrap(
				html_fragments.minecraft_registration_wrap_open(is_whitelisted),
				html_fragments.minecraft_registration_wrap_close(),
				contents=(step_box(container_class="login-container no-glow", contents=(
					step_heading("Minecraft Whitelist Request", 3),
					step_form(
						form_id="minecraft-registration-form",
						class_name="form",
						contents=(
							*contact_fields,
							*hidden_contact,
							step_text_input_group("Minecraft Username", "mc_username", placeholder="Your in-game name"),
							step_dropdown_group(
								label="Who are you?",
								name="who_are_you",
								options=[
									("friend", "Friend or family"),
									("colleague", "Colleague / work contact"),
									("community", "Community member"),
									("other", "Other"),
								],
							),
							step_textarea_group("Additional Information", "additional_info", placeholder="Tell us a bit about yourself..."),
							step_submit_button(
								"Submit Request",
								submission_fields=submission_fields,
								submission_route="/minecraft-registration",
								submission_method="POST",
								success_redirect="",
								failure_redirect=None,
							),
							step_form_message_area(),
						),
					),
				)),),
			),
			add_return_home,
		),
	))

def build_psql_interface_page(user: dict | None) -> str:
	if not user:
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Database Admin"),
				step_error_header(401, "Login required."),
				add_return_home,
			),
		))

	if not interface.is_admin(user.get("id")):
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Database Admin"),
				step_error_header(403, "Admin access required."),
				add_return_home,
			),
		))

	navbar_config = "navbar_landing.json"
	if interface.is_admin(user.get("id")):
		navbar_config = "navbar_landing_admin.json"
	builder = PageBuilder(navbar_config=navbar_config, user=user)
	builder._b.scripts.add("/static/js/form_submit.js")
	builder._b.scripts.add("/static/js/db_interface_resize.js")
	builder._b.scripts.add("/static/js/db_interface_actions.js")
	builder._b.scripts.add("/static/js/db_interface_userid.js")
	builder._b.stylesheets.add("/static/css/forms.css")
	builder._b.stylesheets.add("/static/css/db_interface.css")
	builder.add_html(html_fragments.db_admin_open())
	builder.add_html(html_fragments.heading("Database Admin", 1))
	builder.add_html(html_fragments.db_admin_message())

	schema = "public"
	user_lookup = {}
	user_options = []
	try:
		user_rows, _ = interface.client.get_rows_with_filters("users", page_limit=1000, page_num=0)
		for u in user_rows:
			uid = u.get("id")
			if uid:
				user_lookup[str(uid)] = u
				label_bits = [
					f"{u.get('first_name','')} {u.get('last_name','')}".strip(),
					u.get("email", ""),
				]
				label = " — ".join([b for b in label_bits if b])
				user_options.append({
					"id": str(uid),
					"label": label,
				})
	except Exception:
		user_lookup = {}
		user_options = []

	if user_options:
		options_json = json.dumps(user_options)
		options_b64 = base64.b64encode(options_json.encode("utf-8")).decode("ascii")
		builder.add_html(html_fragments.db_user_id_options_script(options_b64))

	tables = interface.client.list_tables(schema)
	enum_map: dict[str, dict[str, list[str]]] = {}
	tables_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "sql", "tables"))
	try:
		for filename in os.listdir(tables_dir):
			if not filename.endswith(".json"):
				continue
			with open(os.path.join(tables_dir, filename), "r", encoding="utf-8") as handle:
				cfg = json.load(handle)
			tables_cfg = []
			if isinstance(cfg, list):
				tables_cfg = cfg
			elif isinstance(cfg, dict) and "tables" in cfg and isinstance(cfg["tables"], list):
				tables_cfg = cfg["tables"]
			elif isinstance(cfg, dict) and "table_name" in cfg:
				tables_cfg = [cfg]
			for tcfg in tables_cfg:
				tname = tcfg.get("table_name")
				if not tname:
					continue
				for col in tcfg.get("columns", []):
					enum_vals = col.get("enum")
					if not enum_vals:
						continue
					enum_map.setdefault(tname, {})[col["name"]] = [str(v) for v in enum_vals]
	except Exception:
		enum_map = {}

	for table in tables:
		columns = interface.client.get_table_columns(schema, table)
		col_info = interface.client.get_column_info(schema, table)
		pk_cols = interface.client.get_primary_key_columns(schema, table)
		table_enums = enum_map.get(table, {})
		rows, _ = interface.client.get_rows_with_filters(
			f"{schema}.{table}",
			page_limit=200,
			page_num=0,
		)

		builder.add_html(html_fragments.db_section_open(table))

		if not pk_cols:
			builder.add_html(html_fragments.db_section_no_pk())
			continue

		grid_cols = " ".join(["minmax(0, 1fr)"] * len(columns) + ["160px"])
		col_types = ",".join([(col_info.get(c, {}).get("data_type") or "") for c in columns])
		pk_cols_attr = ",".join(pk_cols)
		builder.add_html(html_fragments.db_grid_open(
			grid_cols=grid_cols,
			col_count=len(columns),
			columns=columns,
			col_types=col_types,
			pk_cols_attr=pk_cols_attr,
		))

		# Header
		builder.add_html(html_fragments.db_grid_head_row(columns))

		if not rows:
			builder.add_html(html_fragments.db_grid_empty_row())
		else:
			for row in rows:
				field_names = ["table", "schema"]
				field_names.extend([f"pk__{pk}" for pk in pk_cols])
				field_names.extend([f"col__{col}" for col in columns])
				fields_attr = html.escape(", ".join(field_names))

				builder.add_html(html_fragments.db_row_form_open())
				builder.add_html(HTMLHelper.hidden_input("table", html.escape(table)))
				builder.add_html(HTMLHelper.hidden_input("schema", html.escape(schema)))

				for pk in pk_cols:
					pk_val = row.get(pk)
					builder.add_html(HTMLHelper.hidden_input(
						f"pk__{html.escape(pk)}",
						html.escape(str(pk_val) if pk_val is not None else ""),
					))

				for i, col in enumerate(columns):
					val = row.get(col)
					val_str = "" if val is None else str(val)
					max_len = col_info.get(col, {}).get("character_maximum_length")
					col_type = (col_info.get(col, {}).get("data_type") or "").lower()
					enum_vals = table_enums.get(col)
					if enum_vals:
						options_html = html_fragments.db_enum_options(enum_vals, selected=val_str, include_blank=True)
						builder.add_html(html_fragments.db_cell_enum(i, col, options_html))
					elif col_type == "boolean":
						builder.add_html(html_fragments.db_cell_checkbox(i, col, bool(val)))
					else:
						tooltip_attr = ""
						tooltip_class = ""
						if col == "user_id" and val is not None:
							u = user_lookup.get(str(val))
							if u:
								title_bits = [
									f"{u.get('first_name', '')} {u.get('last_name', '')}".strip(),
									u.get("email", ""),
								]
								tooltip_attr = f' data-tooltip="{html.escape(" | ".join([b for b in title_bits if b]))}"'
								tooltip_class = " db-cell--tooltip"
						builder.add_html(html_fragments.db_cell_text(
							i=i,
							col=col,
							val_str=val_str,
							max_len=int(max_len) if max_len else None,
							user_id_input=col.endswith("user_id") and bool(user_options),
							tooltip_attr=tooltip_attr,
							tooltip_class=tooltip_class,
						))

				builder.add_html(html_fragments.db_actions_cell(fields_attr))
				builder.add_html(html_fragments.db_row_form_close())

		# Insert form
		builder.add_html(html_fragments.db_add_row_head())
		insert_fields = ["table", "schema"]
		insert_fields.extend([f"col__{col}" for col in columns])
		insert_fields_attr = html.escape(", ".join(insert_fields))
		builder.add_html(html_fragments.db_row_add_open())
		builder.add_html(HTMLHelper.hidden_input("table", html.escape(table)))
		builder.add_html(HTMLHelper.hidden_input("schema", html.escape(schema)))

		for i, col in enumerate(columns):
			max_len = col_info.get(col, {}).get("character_maximum_length")
			col_type = (col_info.get(col, {}).get("data_type") or "").lower()
			enum_vals = table_enums.get(col)
			if enum_vals:
				options_html = html_fragments.db_enum_options(enum_vals, include_blank=True)
				builder.add_html(html_fragments.db_cell_enum(i, col, options_html))
			elif col_type == "boolean":
				builder.add_html(html_fragments.db_cell_checkbox(i, col, False))
			else:
				builder.add_html(html_fragments.db_cell_text(
					i=i,
					col=col,
					val_str="",
					max_len=int(max_len) if max_len else None,
					user_id_input=col.endswith("user_id") and bool(user_options),
					tooltip_attr="",
					tooltip_class="",
				))

		builder.add_html(html_fragments.db_add_actions_cell(insert_fields_attr))
		builder.add_html(html_fragments.db_row_add_close())

		builder.add_html(html_fragments.db_grid_close())
		builder.add_html(html_fragments.db_section_close())

	builder.add_html(html_fragments.db_admin_close())
	return builder.render()

def build_admin_email_debug_page(user: dict | None) -> str:
	if not user:
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Debug Email"),
				step_error_header(401, "Login required."),
				add_return_home,
			),
		))

	if not interface.is_admin(user.get("id")):
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Debug Email"),
				step_error_header(403, "Admin access required."),
				add_return_home,
			),
		))

	builder = PageBuilder(navbar_config="navbar_landing_admin.json", user=user)
	builder._b.scripts.add("/static/js/form_submit.js")
	builder._b.stylesheets.add("/static/css/forms.css")
	builder._b.stylesheets.add("/static/css/centering.css")
	builder._b.set_page_title("Debug Email")

	builder.add_html(
		html_fragments.center_column(
			html_fragments.email_debug_form() + html_fragments.email_debug_script()
		)
	)
	return builder.render()

def build_admin_dashboard_page(user: dict | None) -> str:
	if not user:
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Admin Dashboard"),
				step_error_header(401, "Login required."),
				add_return_home,
			),
		))

	if not interface.is_admin(user.get("id")):
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Admin Dashboard"),
				step_error_header(403, "Admin access required."),
				add_return_home,
			),
		))

	builder = PageBuilder(navbar_config="navbar_landing_admin.json", user=user)
	builder._b.stylesheets.add("/static/css/admin_dashboard.css")
	builder._b.set_page_title("Admin Dashboard")

	def _pending_count(table: str) -> int | None:
		try:
			rows = interface.client.execute_query(
				f"SELECT COUNT(*) AS cnt FROM {table} WHERE status = %s;",
				("pending",),
			) or []
			return int(rows[0]["cnt"]) if rows else 0
		except Exception:
			return None

	count_audiobookshelf = _pending_count("audiobookshelf_registrations")
	count_webhook = _pending_count("discord_webhook_registrations")
	count_minecraft = _pending_count("minecraft_registrations")

	cards_html = (
		html_fragments.admin_card(
			"/psql-interface",
			html_fragments.admin_card_meta("Database"),
			"Database Interface",
			"View and edit database tables.",
		)
		+ html_fragments.admin_card(
			"/admin/audiobookshelf-approvals",
			html_fragments.admin_card_meta(
				"Approvals",
				html_fragments.admin_badge_count(count_audiobookshelf),
			),
			"Audiobookshelf Requests",
			"Review account registrations.",
		)
		+ html_fragments.admin_card(
			"/admin/discord-webhook-approvals",
			html_fragments.admin_card_meta(
				"Approvals",
				html_fragments.admin_badge_count(count_webhook),
			),
			"Discord Webhook Requests",
			"Review webhook registration requests.",
		)
		+ html_fragments.admin_card(
			"/admin/minecraft-approvals",
			html_fragments.admin_card_meta(
				"Approvals",
				html_fragments.admin_badge_count(count_minecraft),
			),
			"Minecraft Requests",
			"Review Minecraft whitelist requests.",
		)
		+ html_fragments.admin_card(
			"/admin/email-debug",
			html_fragments.admin_card_meta("Tools"),
			"Debug Email",
			"Send a test email from the system.",
		)
	)
	builder.add_html(html_fragments.admin_dashboard(cards_html))
	return builder.render()

def _get_user_status_label(user_id: str | None, user_cache: dict[str, dict]) -> tuple[str, dict]:
	if not user_id:
		return "Anonymous", {}
	uid = str(user_id)
	if uid not in user_cache:
		user_rows, _ = interface.client.get_rows_with_filters(
			"users",
			equalities={"id": uid},
			page_limit=1,
			page_num=0,
		)
		user_cache[uid] = user_rows[0] if user_rows else {}
	user_row = user_cache.get(uid, {})
	if user_row.get("is_anonymous"):
		return "Anonymous", user_row
	if interface.is_admin(uid):
		return "Admin", user_row
	return "Member", user_row


def build_admin_audiobookshelf_approvals_page(user: dict | None) -> str:
	if not user:
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Audiobookshelf Approvals"),
				step_error_header(401, "Login required."),
				add_return_home,
			),
		))

	if not interface.is_admin(user.get("id")):
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Audiobookshelf Approvals"),
				step_error_header(403, "Admin access required."),
				add_return_home,
			),
		))

	navbar_config = "navbar_landing_admin.json"
	builder = PageBuilder(navbar_config=navbar_config, user=user)
	builder._b.scripts.add("/static/js/form_submit.js")
	builder._b.scripts.add("/static/js/admin_approvals.js")
	builder._b.stylesheets.add("/static/css/forms.css")
	builder._b.stylesheets.add("/static/css/centering.css")
	builder.add_html(html_fragments.heading("Audiobookshelf Approvals", 1))

	rows, _ = interface.client.get_rows_with_filters(
		"audiobookshelf_registrations",
		equalities={"status": "pending"},
		page_limit=200,
		page_num=0,
		order_by="created_at",
		order_dir="DESC",
	)
	user_cache: dict[str, dict] = {}

	if not rows:
		builder.add_html(html_fragments.paragraph("No pending requests."))
		return builder.render()

	for r in rows:
		status_label, _ = _get_user_status_label(r.get("user_id"), user_cache)
		name = f"{r['first_name']} {r['last_name']}".strip()
		email = r["email"]
		additional = r.get("additional_info") or ""
		rows_html = (
			html_fragments.approval_row("Email", html.escape(email)) +
			html_fragments.approval_row("Additional info", html.escape(additional) if additional else "—", full=True)
		)
		actions_html = html_fragments.approval_actions(
			"/api/admin/audiobookshelf/approve",
			"/api/admin/audiobookshelf/deny",
			str(r["id"]),
		)
		builder.add_html(
			html_fragments.approval_card(
				html.escape(name),
				"Audiobookshelf Request",
				html.escape(status_label),
				rows_html,
				actions_html,
			)
		)

	return builder.render()


def build_admin_discord_webhook_approvals_page(user: dict | None) -> str:
	if not user:
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Discord Webhook Approvals"),
				step_error_header(401, "Login required."),
				add_return_home,
			),
		))

	if not interface.is_admin(user.get("id")):
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Discord Webhook Approvals"),
				step_error_header(403, "Admin access required."),
				add_return_home,
			),
		))

	navbar_config = "navbar_landing_admin.json"
	builder = PageBuilder(navbar_config=navbar_config, user=user)
	builder._b.scripts.add("/static/js/form_submit.js")
	builder._b.scripts.add("/static/js/admin_approvals.js")
	builder._b.stylesheets.add("/static/css/forms.css")
	builder._b.stylesheets.add("/static/css/centering.css")
	builder.add_html(html_fragments.heading("Discord Webhook Approvals", 1))

	rows, _ = interface.client.get_rows_with_filters(
		"discord_webhook_registrations",
		equalities={"status": "pending"},
		page_limit=200,
		page_num=0,
		order_by="created_at",
		order_dir="DESC",
	)

	user_cache: dict[str, dict] = {}

	if not rows:
		builder.add_html(html_fragments.paragraph("No pending requests."))
		return builder.render()

	for r in rows:
		submitted = ""
		submitted_at = ""
		uid = ""
		if r.get("created_at"):
			try:
				submitted_at = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
			except Exception:
				submitted_at = str(r["created_at"])
		if submitted_at:
			submitted_at = f"{html.escape(submitted_at)}"
		if r.get("submitted_by_name") or r.get("submitted_by_email"):
			name = r.get("submitted_by_name") or "Unknown"
			email = r.get("submitted_by_email") or "Unknown"
			submitted = f"{html.escape(name)} — {html.escape(email)}"
			status_label = "Anonymous"
		elif r.get("submitted_by_user_id"):
			uid = str(r["submitted_by_user_id"])
			status_label, u = _get_user_status_label(uid, user_cache)
			name = f"{u.get('first_name','')} {u.get('last_name','')}".strip() or "Unknown"
			email = u.get("email") or "Unknown"
			submitted = f"{html.escape(name)} — {html.escape(email)}"
		else:
			status_label = "Anonymous"
		rows_html = (
			html_fragments.approval_row("Webhook URL", html.escape(r["webhook_url"])) +
			html_fragments.approval_row("Submitted by", submitted or "Anonymous") +
			html_fragments.approval_row("Submitted at", submitted_at or "—") +
			html_fragments.approval_row("User ID", html.escape(uid) if r.get("submitted_by_user_id") else "—")
		)
		actions_html = html_fragments.approval_actions(
			"/api/admin/discord-webhook/approve",
			"/api/admin/discord-webhook/deny",
			str(r["id"]),
		)
		builder.add_html(
			html_fragments.approval_card(
				html.escape(r["name"]),
				f"Event key: {html.escape(r['event_key'])}",
				html.escape(status_label),
				rows_html,
				actions_html,
			)
		)

	return builder.render()


def build_admin_minecraft_approvals_page(user: dict | None) -> str:
	if not user:
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Minecraft Approvals"),
				step_error_header(401, "Login required."),
				add_return_home,
			),
		))

	if not interface.is_admin(user.get("id")):
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Minecraft Approvals"),
				step_error_header(403, "Admin access required."),
				add_return_home,
			),
		))

	builder = PageBuilder(navbar_config="navbar_landing_admin.json", user=user)
	builder._b.scripts.add("/static/js/admin_approvals.js")
	builder._b.stylesheets.add("/static/css/forms.css")
	builder._b.stylesheets.add("/static/css/centering.css")
	builder.add_html(html_fragments.heading("Minecraft Approvals", 1))

	rows, _ = interface.client.get_rows_with_filters(
		"minecraft_registrations",
		equalities={"status": "pending"},
		page_limit=200,
		page_num=0,
		order_by="created_at",
		order_dir="DESC",
	)
	user_cache: dict[str, dict] = {}

	if not rows:
		builder.add_html(html_fragments.paragraph("No pending requests."))
		return builder.render()

	for r in rows:
		submitted_at = ""
		if r.get("created_at"):
			try:
				submitted_at = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
			except Exception:
				submitted_at = str(r["created_at"])
		submitted_at = html.escape(submitted_at) if submitted_at else ""
		status_label, _ = _get_user_status_label(r.get("user_id"), user_cache)

		rows_html = (
			html_fragments.approval_row("Name", f"{html.escape(r['first_name'])} {html.escape(r['last_name'])}") +
			html_fragments.approval_row("Email", html.escape(r["email"])) +
			html_fragments.approval_row("Submitted at", submitted_at or "—") +
			html_fragments.approval_row("Additional info", html.escape(r.get("additional_info") or "") or "—", full=True)
		)
		actions_html = html_fragments.approval_actions(
			"/api/admin/minecraft/approve",
			"/api/admin/minecraft/deny",
			str(r["id"]),
		)
		builder.add_html(
			html_fragments.approval_card(
				html.escape(r["mc_username"]),
				html.escape(r["who_are_you"]),
				html.escape(status_label),
				rows_html,
				actions_html,
			)
		)

	return builder.render()

def build_error_page(user: dict | None, e) -> str:
	if not hasattr(e, 'code') or not hasattr(e, 'description'):
		e.code = 500
		e.description = "An unexpected error occurred."
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title(f"{e.code} Error"),
			step_error_header(e.code, e.description),
			add_return_home,
		),
	))


def build_501_page(user: dict | None = None) -> str:
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("501 Not Implemented"),
			step_error_header(501, "Not Implemented"),
			step_text_paragraph("The requested functionality is not yet implemented on this server."),
			add_return_home,
		),
	))
