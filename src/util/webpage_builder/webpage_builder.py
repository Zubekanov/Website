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
from util.navbars.visibility import filter_nav_items

fcr = FileConfigReader()
interface = PSQLInterface()

class PageBuilder:
	def __init__(
		self,
		*,
		page_config: str = "default",
		navbar_config: str = "auto",
		user: dict | None = None,
	):
		self._b = parent_builder.WebPageBuilder()
		self._b.load_page_config(page_config)
		is_admin = _is_admin_user(user)
		resolved_nav = resolve_navbar_config(user, navbar_config, is_admin=is_admin)
		self._b._build_nav_html(resolved_nav, user=user, is_admin=is_admin)

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
	navbar_config: str = "auto"
	steps: tuple[Step, ...] = ()


def build_page(user: dict | None, spec: PageSpec) -> str:
	builder = PageBuilder(page_config=spec.page_config, navbar_config=spec.navbar_config, user=user)
	for step in spec.steps:
		step(builder)
	return builder.render()


def _is_admin_user(user: dict | None) -> bool:
	if not user:
		return False
	try:
		return bool(interface.is_admin(user.get("id")))
	except Exception:
		return False


def resolve_navbar_config(user: dict | None, navbar_config: str | None, *, is_admin: bool | None = None) -> str:
	_ = user
	_ = is_admin
	if not navbar_config or navbar_config == "auto":
		return "navbar_landing.json"
	if navbar_config == "navbar_landing_admin.json":
		return "navbar_landing.json"
	return navbar_config

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
	m = _MD_LIST_ITEM_RE.match(lines[start])
	if not m:
		return "", start
	list_type = _list_type(m.group("marker"))
	html_parts = [f"<{list_type}>"]
	i = start
	while i < len(lines):
		m = _MD_LIST_ITEM_RE.match(lines[i])
		if not m:
			break
		indent = _indent_len(lines[i])
		if indent < base_indent:
			break
		if indent > base_indent:
			break
		if _list_type(m.group("marker")) != list_type:
			break

		body = m.group("body").strip()
		html_parts.append(f"<li>{_md_inline(body)}")
		i += 1

		continuation: list[str] = []
		while i < len(lines):
			if not lines[i].strip():
				continuation.append("")
				i += 1
				continue
			m2 = _MD_LIST_ITEM_RE.match(lines[i])
			if m2:
				indent2 = _indent_len(lines[i])
				if indent2 > base_indent:
					break
				if indent2 <= base_indent:
					break
			indent2 = _indent_len(lines[i])
			if indent2 > base_indent:
				continuation.append(lines[i].strip())
				i += 1
				continue
			break

		if continuation:
			cont_text = " ".join([c for c in continuation if c]).strip()
			if cont_text:
				html_parts.append(f"<p>{_md_inline(cont_text)}</p>")

		if i < len(lines):
			m2 = _MD_LIST_ITEM_RE.match(lines[i])
			if m2 and _indent_len(lines[i]) > base_indent:
				nested_html, i = _parse_list(lines, i, _indent_len(lines[i]))
				if nested_html:
					html_parts.append(nested_html)

		html_parts.append("</li>")

	html_parts.append(f"</{list_type}>")
	return "".join(html_parts), i


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

		if _MD_LIST_ITEM_RE.match(line):
			flush_paragraph()
			list_html, next_idx = _parse_list(lines, i, _indent_len(line))
			if list_html:
				blocks.append(list_html)
				i = next_idx
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

def step_profile_content(
	profile_card_html: str,
	integrations_html: str,
	modal_html: str,
) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(
			html_fragments.profile_page_shell(
				profile_card_html + integrations_html + modal_html
			)
		)
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

def step_add_stylesheets(*paths: str) -> Step:
	def _step(builder: PageBuilder):
		for path in paths:
			if path:
				builder._b.stylesheets.add(path)
	return _step

def step_add_scripts(*paths: str) -> Step:
	def _step(builder: PageBuilder):
		for path in paths:
			if path:
				builder._b.scripts.add(path)
	return _step

def step_metrics_dashboard(kpi_html: str) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(html_fragments.metrics_dashboard_open())
		builder.add_html(kpi_html)
		builder.add_html(html_fragments.metrics_dashboard_between_sections())
	return _step

def step_admin_users_content(cards_html: str) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(html_fragments.admin_users_shell(cards_html))
		builder.add_html(html_fragments.integration_delete_modal(
			html_fragments.integration_delete_reason_select(
				[
					("", "Select a reason"),
					("admin", "Admin action"),
					("policy", "Policy violation"),
					("security", "Security concern"),
					("other", "Other"),
				]
			)
		))
		builder.add_html(html_fragments.admin_user_delete_modal(
			html_fragments.admin_user_delete_reason_select(
				[
					("", "Select a reason"),
					("requested", "User requested removal"),
					("policy", "Policy violation"),
					("security", "Security concern"),
					("duplicate", "Duplicate account"),
					("other", "Other"),
				]
			)
		))
	return _step

def step_admin_dashboard_content(cards_html: str) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(html_fragments.admin_dashboard(cards_html))
	return _step

def step_admin_approvals_content(
	title: str,
	cards_html: str,
	*,
	empty_message: str = "No pending requests.",
) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(html_fragments.heading(title, 1))
		if cards_html:
			builder.add_html(cards_html)
		else:
			builder.add_html(html_fragments.paragraph(empty_message))
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
	except Exception:
		readme_text = "README is currently unavailable."

	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("README.md"),
			step_centering(
				max_width="900px",
				contents=(
					step_heading("README.md", 1),
					step_markdown_block(readme_text),
					),
			),
		),
	))

def build_empty_landing_page(user: dict | None) -> str:
	nav_config = fcr.find("navbar_landing.json")
	items = filter_nav_items(
		nav_config.get("items", []),
		user,
		_is_admin_user(user),
	)
	section_cards: list[str] = []

	def _landing_nav_hero(title: str, lead: str) -> str:
		return (
			"<div class=\"landing-nav__hero\">"
			f"<h2>{html.escape(title)}</h2>"
			f"<p class=\"landing-nav__lead\">{html.escape(lead)}</p>"
			"</div>"
		)

	def _landing_nav_shell(hero_html: str, cards_html: str) -> str:
		return (
			"<section class=\"landing-nav\">"
			f"{hero_html}"
			"<div class=\"landing-nav__grid\">"
			f"{cards_html}"
			"</div>"
			"</section>"
		)

	def _card(title: str, links_html: str, *, class_name: str = "") -> str:
		card_class = "landing-nav__card"
		if class_name:
			card_class = f"{card_class} {class_name}"
		return (
			f"<div class=\"{card_class}\">"
			f"<h3>{html.escape(title)}</h3>"
			f"<div class=\"landing-nav__links\">{links_html}</div>"
			"</div>"
		)

	def _link(label: str, desc: str, href: str) -> str:
		escaped_desc = html.escape(desc)
		escaped_desc = escaped_desc.replace("&lt;br&gt;", "\n").replace("&lt;br/&gt;", "\n").replace("&lt;br /&gt;", "\n")
		return (
			f"<a class=\"landing-nav__link\" href=\"{html.escape(href)}\">"
			f"<span class=\"landing-nav__label\">{html.escape(label)}</span>"
			f"<span class=\"landing-nav__desc\">{escaped_desc}</span>"
			"</a>"
		)

	quick_links: list[str] = [
		_link("Login", "Sign in to your account.", "/login"),
		_link("Register", "Create an account to access more features.", "/register"),
	]
	for item in items:
		item_type = item.get("type")
		if item_type == "link":
			quick_links.append(_link(item.get("label", ""), item.get("desc", ""), item.get("href", "#")))
			continue
		if item_type != "mega":
			continue
		for section in item.get("sections", []):
			section_type = section.get("type")
			if section_type == "github_repos":
				username = section.get("username", "")
				limit = int(section.get("limit", 6) or 6)
				repos, total = parent_builder.fetch_github_repos(username, limit=limit) if username else ([], 0)
				links = [
					_link(
						r.get("label", ""),
						r.get("desc", ""),
						r.get("href", "#"),
					)
					for r in repos
				]
				more_count = max(total - len(repos), 0)
				if more_count:
					links.append(_link(
						f"{more_count} more repos publicly available",
						"View all repositories on GitHub.",
						f"https://github.com/{username}?tab=repositories",
					))
				section_cards.append(_card(
					section.get("label", "GitHub Repositories"),
					"".join(links),
					class_name="landing-nav__card--github",
				))
				continue
			links = [
				_link(
					entry.get("label", ""),
					entry.get("desc", ""),
					entry.get("href", "#"),
				)
				for entry in section.get("items", [])
			]
			section_cards.append(_card(section.get("label", item.get("label", "Explore")), "".join(links)))

	if quick_links:
		section_cards.insert(0, _card("Quick Links", "".join(quick_links)))

	hero_html = _landing_nav_hero(
		"This is the collection of features implemented on the website.",
		"Thank you for visiting!",
	)
	page_html = _landing_nav_shell(hero_html, "".join(section_cards))

	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Home"),
			step_add_stylesheets("/static/css/landing_nav.css"),
			step_text_block(page_html),
		),
	))

def build_profile_page(user: dict | None) -> str:
	user_name = f"{user['first_name']} {user['last_name']}"
	is_admin = _is_admin_user(user)
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
				sub_rows = interface.get_discord_webhook_subscriptions(wh.get("id"))
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
					html_fragments.secret_field(wh.get("webhook_url") or "", label="Webhook URL"),
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
	popugame_history_html = _build_profile_popugame_history_html(user)
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
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title(user_name + "'s Profile"),
			step_add_stylesheets("/static/css/profile.css"),
			step_add_scripts("/static/js/copy_tooltip.js", "/static/js/profile_integrations.js", "/static/js/profile_popugame_history.js"),
			step_profile_content(profile_card_html, popugame_history_html + integrations_html, modal_html),
		),
	))


def _build_profile_popugame_history_html(user: dict) -> str:
	try:
		user_id = str(user.get("id"))
		rating_rows, _ = interface.client.get_rows_with_filters(
			"popugame_ratings",
			equalities={"user_id": user_id},
			page_limit=1,
			page_num=0,
		)
		elo = int(rating_rows[0].get("elo") or 1200) if rating_rows else 1200
		history_rows = interface.execute_query(
			"SELECT code, player0_user_id, player1_user_id, player0_name, player1_name, winner, "
			"elo_before_p0, elo_after_p0, elo_delta_p0, "
			"elo_before_p1, elo_after_p1, elo_delta_p1, "
			"COALESCE(last_move_at, updated_at, created_at) AS ts "
			"FROM popugame_sessions "
			"WHERE status = 'finished' AND (player0_user_id = %s OR player1_user_id = %s) "
			"ORDER BY ts DESC LIMIT %s;",
			(user_id, user_id, 20),
		) or []
		wins = 0
		losses = 0
		draws = 0
		boxes: list[dict[str, str]] = []

		def _public_pname(name: object) -> str:
			n = (name or "").strip() if isinstance(name, str) else ""
			if not n:
				return "Unknown"
			return "Anonymous" if n.startswith("anon:") else n

		for r in history_rows:
			is_p0 = str(r.get("player0_user_id") or "") == user_id
			winner = r.get("winner")
			if winner is None:
				outcome = "draw"
				draws += 1
			elif (is_p0 and int(winner) == 0) or ((not is_p0) and int(winner) == 1):
				outcome = "win"
				wins += 1
			else:
				outcome = "loss"
				losses += 1
			opponent = _public_pname(r.get("player1_name") if is_p0 else r.get("player0_name"))
			delta_raw = r.get("elo_delta_p0") if is_p0 else r.get("elo_delta_p1")
			elo_after_raw = r.get("elo_after_p0") if is_p0 else r.get("elo_after_p1")
			elo_before_raw = r.get("elo_before_p0") if is_p0 else r.get("elo_before_p1")
			elo_at_match: int | None = None
			if elo_after_raw is not None:
				elo_at_match = int(elo_after_raw)
			elif elo_before_raw is not None and delta_raw is not None:
				elo_at_match = int(elo_before_raw) + int(delta_raw)

			if delta_raw is None:
				delta_txt = "Unrated game"
			else:
				delta_val = int(delta_raw)
				sign = "+" if delta_val > 0 else ""
				delta_txt = f"ELO {sign}{delta_val}"

			if elo_at_match is None:
				elo_txt = "ELO at match: unavailable"
			else:
				elo_txt = f"ELO at match: {elo_at_match}"
			boxes.append({
				"outcome": outcome,
				"tooltip": f"vs {opponent} | {outcome.upper()} | {elo_txt} | {delta_txt}",
			})
		decisive = wins + losses
		total_wr = (wins * 100.0 / decisive) if decisive > 0 else 0.0
		return html_fragments.profile_popugame_history_card(
			elo=elo,
			total_wr=total_wr,
			wins=wins,
			losses=losses,
			draws=draws,
			boxes=boxes,
		)
	except Exception:
		return html_fragments.profile_popugame_history_card(
			elo=1200,
			total_wr=0.0,
			wins=0,
			losses=0,
			draws=0,
			boxes=[],
		)


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

def build_api_access_application_page(user: dict | None) -> str:
	contact_fields: tuple[Step, ...] = ()
	hidden_contact: tuple[Step, ...] = ()
	submission_fields = [
		"first_name",
		"last_name",
		"email",
		"principal_type",
		"service_name",
		"requested_scopes",
		"use_case",
	]
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

	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("API Access Application"),
			step_add_scripts("/static/js/api_scope_selector.js"),
			step_box(contents=(
				step_heading("API Access Application", 2),
				step_form(
					form_id="api-access-application-form",
					class_name="form",
					contents=(
						*contact_fields,
						*hidden_contact,
						step_dropdown_group(
							label="Principal Type",
							name="principal_type",
							options=[
								("service", "Service / system"),
								("user", "User principal"),
							],
						),
						step_text_input_group("Service Name", "service_name", placeholder="Service name"),
						step_form_group(html_fragments.api_scope_selector_input([
							("metrics.read", "metrics.read"),
							("webhook.write", "webhook.write"),
							("webhook.read", "webhook.read"),
							("admin.api", "admin.api"),
						])),
						step_textarea_group("Use Case", "use_case", placeholder="Describe your request."),
						step_submit_button(
							"Submit Application",
							submission_fields=submission_fields,
							submission_route="/api-access-application",
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
	kpi_html = "".join(
		html_fragments.metrics_kpi_card(key, label)
		for key, label in METRICS_NAMES.items()
	)
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Server Metrics"),
			step_add_stylesheets("/static/css/metrics_dashboard.css"),
			step_metrics_dashboard(kpi_html),
			step_metrics_grid,
			step_text_block(html_fragments.metrics_dashboard_close()),
		),
	))

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
			rows = interface.get_active_minecraft_whitelist_usernames(user.get("id"))
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
		builder._b.scripts.add("/static/js/copy_tooltip.js")
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

def build_popugame_page(user: dict | None, *, game_code: str | None = None) -> str:
	def load_popugame_rules_markdown() -> str:
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

	rules_md = load_popugame_rules_markdown()

	def add_popugame_assets(builder: PageBuilder):
		builder._b.stylesheets.add("/static/css/popugame.css")
		builder._b.stylesheets.add("/static/css/minecraft.css")
		builder._b.stylesheets.add("/static/css/markdown.css")
		builder._b.scripts.add("/static/js/copy_tooltip.js")
		builder._b.scripts.add("/static/js/popugame.js")

	def step_popugame_shell_open() -> Step:
		def _step(builder: PageBuilder):
			code_attr = f' data-popugame-code="{html.escape(game_code)}"' if game_code else ""
			builder.add_html(f'<div class="popugame-shell" data-popugame data-size="9" data-turn-limit="40"{code_attr}>')
		return _step

	def step_popugame_shell_close() -> Step:
		def _step(builder: PageBuilder):
			builder.add_html("</div>")
		return _step

	def step_popugame_header() -> Step:
		return step_text_block("""
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
					<div class="popugame__status" data-popugame-status>Player 1 (X) to move</div>
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
			</div>
		</div>
		""")

	def step_popugame_board() -> Step:
		return step_text_block('<div class="popugame__board" data-popugame-board aria-label="PopuGame board"></div>')

	def step_popugame_controls() -> Step:
		if game_code:
			return step_text_block("""
			<div class="popugame__controls">
				<div class="popugame__controls-group popugame__controls-group--play">
					<button class="btn popugame__btn btn--ghost" type="button" data-popugame-rules>Rules</button>
					<button class="btn popugame__btn btn--danger" type="button" data-popugame-concede>Concede Game</button>
				</div>
				<div class="popugame__controls-group popugame__controls-group--multi">
					<button class="btn popugame__btn btn--primary" type="button" data-popugame-host data-popugame-postgame hidden>Host Multiplayer Game</button>
					<button class="btn popugame__btn" type="button" data-popugame-join data-popugame-postgame hidden>Join via Code</button>
				</div>
			</div>
			""")
		return step_text_block("""
		<div class="popugame__controls">
			<div class="popugame__controls-group popugame__controls-group--play">
				<button class="btn popugame__btn btn--ghost" type="button" data-popugame-rules>Rules</button>
				<button class="btn popugame__btn btn--accent" type="button" data-popugame-undo disabled>Undo Move</button>
				<button class="btn popugame__btn" type="button" data-popugame-reset>Reset Board</button>
			</div>
			<div class="popugame__controls-group popugame__controls-group--multi">
				<button class="btn popugame__btn btn--primary" type="button" data-popugame-host>Host Multiplayer Game</button>
				<button class="btn popugame__btn" type="button" data-popugame-join>Join via Code</button>
			</div>
		</div>
		""")

	def step_popugame_share_panel() -> Step:
		link = f"/popugame/{game_code}" if game_code else ""
		link_safe = html.escape(link)
		code_safe = html.escape(game_code or "")
		return step_text_block(f"""
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
		""")

	def step_popugame_rules_modal() -> Step:
		return step_text_block("""
		<div class="popugame__backdrop" data-popugame-modal aria-hidden="true">
			<div class="popugame__modal" role="dialog" aria-modal="true" aria-labelledby="popugame-rules-title">
				<div class="popugame__modal-header">
					<h3 id="popugame-rules-title">PopuGame Rules</h3>
					<button class="popugame__close" type="button" data-popugame-close aria-label="Close rules">×</button>
				</div>
				<div class="popugame__modal-body">
					""" + f"<div class=\"markdown-block popugame__rules-markdown\">{render_markdown(rules_md)}</div>" + """
				</div>
			</div>
		</div>
		""")

	def step_popugame_dialog_modal() -> Step:
		return step_text_block("""
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
		""")

	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("PopuGame"),
			add_popugame_assets,
			step_popugame_shell_open(),
			step_popugame_header(),
			*( (step_popugame_share_panel(),) if game_code else () ),
			step_popugame_board(),
			step_popugame_controls(),
			step_popugame_rules_modal(),
			step_popugame_dialog_modal(),
			step_popugame_shell_close(),
			add_return_home,
		),
	))


def build_popugame_invalid_link_page(user: dict | None) -> str:
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Invalid PopuGame Link"),
			step_heading("Invalid PopuGame Link", 2),
			step_text_paragraph("This PopuGame link is invalid or no longer available."),
			step_text_paragraph("Please host a new game or ask the host for a fresh link/code."),
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
		user_options_script = html_fragments.db_user_id_options_script(options_b64)
	else:
		user_options_script = ""

	html_parts = [
		html_fragments.db_admin_open(),
		html_fragments.heading("Database Admin", 1),
		html_fragments.db_admin_message(),
	]
	if user_options_script:
		html_parts.append(user_options_script)

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

		html_parts.append(html_fragments.db_section_open(table))

		if not pk_cols:
			html_parts.append(html_fragments.db_section_no_pk())
			continue

		grid_cols = " ".join(["minmax(0, 1fr)"] * len(columns) + ["160px"])
		col_types = ",".join([(col_info.get(c, {}).get("data_type") or "") for c in columns])
		pk_cols_attr = ",".join(pk_cols)
		html_parts.append(html_fragments.db_grid_open(
			grid_cols=grid_cols,
			col_count=len(columns),
			columns=columns,
			col_types=col_types,
			pk_cols_attr=pk_cols_attr,
		))

		# Header
		html_parts.append(html_fragments.db_grid_head_row(columns))

		if not rows:
			html_parts.append(html_fragments.db_grid_empty_row())
		else:
			for row in rows:
				field_names = ["table", "schema"]
				field_names.extend([f"pk__{pk}" for pk in pk_cols])
				field_names.extend([f"col__{col}" for col in columns])
				fields_attr = html.escape(", ".join(field_names))

				html_parts.append(html_fragments.db_row_form_open())
				html_parts.append(HTMLHelper.hidden_input("table", html.escape(table)))
				html_parts.append(HTMLHelper.hidden_input("schema", html.escape(schema)))

				for pk in pk_cols:
					pk_val = row.get(pk)
					html_parts.append(HTMLHelper.hidden_input(
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
						html_parts.append(html_fragments.db_cell_enum(i, col, options_html))
					elif col_type == "boolean":
						html_parts.append(html_fragments.db_cell_checkbox(i, col, bool(val)))
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
						html_parts.append(html_fragments.db_cell_text(
							i=i,
							col=col,
							val_str=val_str,
							max_len=int(max_len) if max_len else None,
							user_id_input=col.endswith("user_id") and bool(user_options),
							tooltip_attr=tooltip_attr,
							tooltip_class=tooltip_class,
						))

				html_parts.append(html_fragments.db_actions_cell(fields_attr))
				html_parts.append(html_fragments.db_row_form_close())

		# Insert form
		html_parts.append(html_fragments.db_add_row_head())
		insert_fields = ["table", "schema"]
		insert_fields.extend([f"col__{col}" for col in columns])
		insert_fields_attr = html.escape(", ".join(insert_fields))
		html_parts.append(html_fragments.db_row_add_open())
		html_parts.append(HTMLHelper.hidden_input("table", html.escape(table)))
		html_parts.append(HTMLHelper.hidden_input("schema", html.escape(schema)))

		for i, col in enumerate(columns):
			max_len = col_info.get(col, {}).get("character_maximum_length")
			col_type = (col_info.get(col, {}).get("data_type") or "").lower()
			enum_vals = table_enums.get(col)
			if enum_vals:
				options_html = html_fragments.db_enum_options(enum_vals, include_blank=True)
				html_parts.append(html_fragments.db_cell_enum(i, col, options_html))
			elif col_type == "boolean":
				html_parts.append(html_fragments.db_cell_checkbox(i, col, False))
			else:
				html_parts.append(html_fragments.db_cell_text(
					i=i,
					col=col,
					val_str="",
					max_len=int(max_len) if max_len else None,
					user_id_input=col.endswith("user_id") and bool(user_options),
					tooltip_attr="",
					tooltip_class="",
				))

		html_parts.append(html_fragments.db_add_actions_cell(insert_fields_attr))
		html_parts.append(html_fragments.db_row_add_close())

		html_parts.append(html_fragments.db_grid_close())
		html_parts.append(html_fragments.db_section_close())

	html_parts.append(html_fragments.db_admin_close())
	page_html = "".join(html_parts)
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Database Admin"),
			step_add_stylesheets("/static/css/forms.css", "/static/css/db_interface.css"),
			step_add_scripts(
				"/static/js/form_submit.js",
				"/static/js/db_interface_resize.js",
				"/static/js/db_interface_actions.js",
				"/static/js/db_interface_userid.js",
			),
			step_text_block(page_html),
		),
	))

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

	page_html = html_fragments.center_column(
		html_fragments.email_debug_form() + html_fragments.email_debug_script()
	)
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Debug Email"),
			step_add_stylesheets("/static/css/forms.css", "/static/css/centering.css"),
			step_add_scripts("/static/js/form_submit.js"),
			step_text_block(page_html),
		),
	))

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

	count_audiobookshelf = interface.count_pending_audiobookshelf_registrations()
	count_webhook = interface.count_pending_discord_webhook_registrations()
	count_minecraft = interface.count_pending_minecraft_registrations()
	count_api_access = interface.count_pending_api_access_registrations()

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
			"/admin/api-access-approvals",
			html_fragments.admin_card_meta(
				"Approvals",
				html_fragments.admin_badge_count(count_api_access),
			),
			"API Access Requests",
			"Review API key access applications.",
		)
		+ html_fragments.admin_card(
			"/admin/users",
			html_fragments.admin_card_meta("Accounts"),
			"User Management",
			"View users, roles, and integrations.",
		)
		+ html_fragments.admin_card(
			"/admin/email-debug",
			html_fragments.admin_card_meta("Tools"),
			"Debug Email",
			"Send a test email from the system.",
		)
		+ html_fragments.admin_card(
			"/admin/frontend-test",
			html_fragments.admin_card_meta("Tools"),
			"Frontend Test Page",
			"Preview labeled UI elements and styles.",
		)
	)
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Admin Dashboard"),
			step_add_stylesheets("/static/css/admin_dashboard.css"),
			step_admin_dashboard_content(cards_html),
		),
	))


def build_admin_frontend_test_page(user: dict | None) -> str:
	if not user:
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Frontend Test"),
				step_error_header(401, "Login required."),
				add_return_home,
			),
		))

	if not interface.is_admin(user.get("id")):
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Frontend Test"),
				step_error_header(403, "Admin access required."),
				add_return_home,
			),
		))

	sections = _frontend_test_sections()
	sections_html = "".join([
		"<div class=\"frontend-test-page\">",
		"<h1>Frontend Test Page</h1>",
		"<p class=\"frontend-test-intro\">This page previews UI components with labels for visual regression checks.</p>",
		"".join(_frontend_section_html(section) for section in sections),
		"</div>",
	])
	stylesheets = sorted({
		"/static/css/frontend_test.css",
		*{
			path
			for section in sections
			for path in section.stylesheets
		},
	})

	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Frontend Test"),
			step_add_stylesheets(*stylesheets),
			step_text_block(sections_html),
		),
	))


def _frontend_sample(label: str, content_html: str) -> str:
	return (
		"<article class=\"frontend-sample\">"
		f"<div class=\"frontend-sample__label\">{html.escape(label)}</div>"
		f"<div class=\"frontend-sample__body\">{content_html}</div>"
		"</article>"
	)


@dataclass(frozen=True)
class FrontendSectionSpec:
	title: str
	stylesheets: tuple[str, ...]
	samples: tuple[tuple[str, str], ...]


def _frontend_section_html(section: FrontendSectionSpec) -> str:
	return (
		"<section class=\"frontend-test-section\">"
		f"<h2>{html.escape(section.title)}</h2>"
		"<div class=\"frontend-test-grid\">"
		f"{''.join(_frontend_sample(label, sample_html) for label, sample_html in section.samples)}"
		"</div>"
		"</section>"
	)


def _frontend_test_sections() -> tuple[FrontendSectionSpec, ...]:
	return (
		FrontendSectionSpec(
			title="Buttons",
			stylesheets=("/static/css/forms.css",),
			samples=(
				("HTMLHelper.button default", HTMLHelper.button("Default")),
				("HTMLHelper.button primary", HTMLHelper.button("Primary", variant="primary")),
				("HTMLHelper.button accent", HTMLHelper.button("Accent", variant="accent")),
				("HTMLHelper.button danger", HTMLHelper.button("Danger", variant="danger")),
				("HTMLHelper.button ghost", HTMLHelper.button("Ghost", variant="ghost")),
				("HTMLHelper.button pill sm", HTMLHelper.button("Pill Small", size="sm", shape="pill")),
				("HTMLHelper.button xs", HTMLHelper.button("XS", size="xs")),
				("HTMLHelper.button lg", HTMLHelper.button("Large", size="lg")),
			),
		),
		FrontendSectionSpec(
			title="Form Controls",
			stylesheets=("/static/css/forms.css",),
			samples=(
				("HTMLHelper.text_input", HTMLHelper.form_group(HTMLHelper.text_input("Email", "fe-email", "name@example.com"))),
				("HTMLHelper.password_input", HTMLHelper.form_group(HTMLHelper.password_input("Password", "fe-password", "Password"))),
				("HTMLHelper.select_input", HTMLHelper.form_group(HTMLHelper.select_input("Role", "fe-role", [("member", "Member"), ("admin", "Admin")], "member"))),
				("HTMLHelper.dropdown", HTMLHelper.form_group(HTMLHelper.dropdown("Reason", "fe-reason", [("requested", "Requested"), ("policy", "Policy")], placeholder="Select reason"))),
				("HTMLHelper.textarea_input", HTMLHelper.form_group(HTMLHelper.textarea_input("Notes", "fe-notes", "Add notes", rows=4))),
				("HTMLHelper.checkbox_input", HTMLHelper.form_group(HTMLHelper.checkbox_input("Confirm action", "fe-confirm"))),
				("html_fragments.form_message_area", html_fragments.form_message_area("form-message", 'data-state="success"', 2)),
			),
		),
		FrontendSectionSpec(
			title="Admin Dashboard UI",
			stylesheets=("/static/css/admin_dashboard.css",),
			samples=(
				("html_fragments.admin_badge_count", html_fragments.admin_badge_count(12)),
				("html_fragments.admin_badge_count loading", html_fragments.admin_badge_count(None)),
				("html_fragments.admin_card", html_fragments.admin_card("#", html_fragments.admin_card_meta("Example", html_fragments.admin_badge_count(3)), "Sample Card", "Card typography and spacing preview.")),
				("html_fragments.admin_dashboard", html_fragments.admin_dashboard(html_fragments.admin_card("#", html_fragments.admin_card_meta("Tools"), "Nested Card", "Dashboard shell preview."))),
			),
		),
		FrontendSectionSpec(
			title="Admin Users UI",
			stylesheets=("/static/css/admin_users.css", "/static/css/forms.css", "/static/css/profile.css"),
			samples=(
				("html_fragments.admin_user_badge", html_fragments.admin_user_badge("ADMIN")),
				("html_fragments.admin_user_action_button", html_fragments.admin_user_action_button("promote", "user-123", "Promote")),
				("html_fragments.admin_user_actions", html_fragments.admin_user_actions(html_fragments.admin_user_action_button("delete", "user-123", "Delete user", True))),
				("html_fragments.admin_user_card", html_fragments.admin_user_card("user-123", "Example User", "example@user.com", html_fragments.admin_user_meta_row("Joined", "15 Jan 2026"), html_fragments.admin_user_badge("MEMBER"), html_fragments.admin_user_actions(html_fragments.admin_user_action_button("promote", "user-123", "Promote to admin")), html_fragments.admin_user_integrations(html_fragments.integration_card_empty("No integrations")))),
				("html_fragments.admin_user_delete_modal", html_fragments.admin_user_delete_modal(html_fragments.admin_user_delete_reason_select([("", "Select a reason"), ("policy", "Policy")]))),
			),
		),
		FrontendSectionSpec(
			title="Profile and Integrations UI",
			stylesheets=("/static/css/profile.css", "/static/css/forms.css"),
			samples=(
				("html_fragments.profile_badge member", html_fragments.profile_badge("MEMBER", static=True)),
				("html_fragments.profile_badge admin", html_fragments.profile_badge("ADMIN", static=True)),
				("html_fragments.profile_password_panel", html_fragments.profile_password_panel()),
				("html_fragments.profile_delete_panel", html_fragments.profile_delete_panel()),
				("html_fragments.integration_card", html_fragments.integration_card("discord_webhook", "int-1", "Discord Webhook", "moderator.notifications", "https://discord.com/api/webhooks/...", html_fragments.integration_delete_button("discord_webhook", "int-1", "Discord Webhook", "Active", True))),
				("html_fragments.integration_subscriptions", html_fragments.integration_subscriptions("Subscriptions", html_fragments.subscription_card("moderator.notifications", "all", "Moderation events", "today", "Active", True, html_fragments.subscription_action("unsubscribe", "sub-1", "/api/profile/discord-webhook/unsubscribe", "Unsubscribe"), ""))),
				("html_fragments.integration_delete_modal", html_fragments.integration_delete_modal(html_fragments.integration_delete_reason_select([("", "Select a reason"), ("requested", "Requested")]))),
			),
		),
		FrontendSectionSpec(
			title="Approvals UI",
			stylesheets=("/static/css/centering.css", "/static/css/forms.css"),
			samples=(
				("html_fragments.approval_row", html_fragments.approval_row("Email", "user@example.com")),
				("html_fragments.approval_actions", html_fragments.approval_actions("/api/admin/audiobookshelf/approve", "/api/admin/audiobookshelf/deny", "req-1")),
				("html_fragments.approval_card", html_fragments.approval_card("Request", "Example request", "PENDING", html_fragments.approval_row("Email", "user@example.com") + html_fragments.approval_row("Submitted", "Today"), html_fragments.approval_actions("/api/admin/audiobookshelf/approve", "/api/admin/audiobookshelf/deny", "req-1"))),
			),
		),
		FrontendSectionSpec(
			title="Minecraft UI",
			stylesheets=("/static/css/minecraft.css", "/static/css/forms.css"),
			samples=(
				("html_fragments.minecraft_status_card", html_fragments.minecraft_status_card()),
				("html_fragments.minecraft_whitelist_banner", html_fragments.minecraft_whitelist_banner(True, "zubekanov")),
				("html_fragments.minecraft_registration_wrap", html_fragments.minecraft_registration_wrap_open(False) + "<div>Registration form slot</div>" + html_fragments.minecraft_registration_wrap_close()),
			),
		),
		FrontendSectionSpec(
			title="Metrics UI",
			stylesheets=("/static/css/metrics_dashboard.css", "/static/css/plotly.css"),
			samples=(
				("html_fragments.metrics_dashboard", html_fragments.metrics_dashboard_open() + html_fragments.metrics_kpi_card("cpu", "CPU") + html_fragments.metrics_dashboard_between_sections() + html_fragments.metrics_dashboard_close()),
			),
		),
		FrontendSectionSpec(
			title="DB Interface UI",
			stylesheets=("/static/css/db_interface.css", "/static/css/forms.css"),
			samples=(
				("html_fragments.db_admin_message", html_fragments.db_admin_open() + html_fragments.db_admin_message() + html_fragments.db_admin_close()),
				("html_fragments.db_grid_head_row", html_fragments.db_section_open("Users") + html_fragments.db_grid_open("1.2fr 1.4fr 0.8fr 160px", 3, ["id", "email", "is_active"], "text,text,boolean", "id", 160) + html_fragments.db_grid_head_row(["id", "email", "is_active"]) + html_fragments.db_grid_close() + html_fragments.db_section_close()),
				("html_fragments.db_add_actions_cell", "<table><tbody><tr>" + html_fragments.db_add_actions_cell("table,schema,col__email") + "</tr></tbody></table>"),
			),
		),
		FrontendSectionSpec(
			title="Webhook Verify UI",
			stylesheets=("/static/css/forms.css",),
			samples=(
				("html_fragments.webhook_selector_input", html_fragments.webhook_selector_input("Webhook URL", "fe-wh", "webhook_url", "https://discord.com/api/webhooks/...", "url")),
				("html_fragments.webhook_options_data_script", html_fragments.webhook_options_data_script("W10=")),
			),
		),
	)


def build_admin_users_page(user: dict | None) -> str:
	if not user:
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("User Management"),
				step_error_header(401, "Login required."),
				add_return_home,
			),
		))

	if not interface.is_admin(user.get("id")):
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("User Management"),
				step_error_header(403, "Admin access required."),
				add_return_home,
			),
		))

	user_rows = interface.get_admin_user_management_rows()
	cards_html = []

	for row in user_rows:
		user_id = str(row.get("id") or "")
		first = row.get("first_name") or ""
		last = row.get("last_name") or ""
		email = row.get("email") or ""
		created = row.get("created_at")
		created_str = ""
		if created:
			try:
				created_str = created.strftime("%d %B %Y")
			except Exception:
				created_str = str(created)
		is_anonymous = bool(row.get("is_anonymous"))
		is_active = bool(row.get("is_active", True))
		role_label = "ANONYMOUS" if is_anonymous else ("ADMIN" if interface.is_admin(user_id) else "MEMBER")
		status_label = "Active" if is_active else "Inactive"
		meta_html = (
			html_fragments.admin_user_meta_row("Joined", created_str or "Unknown")
			+ html_fragments.admin_user_meta_row("Status", status_label)
			+ html_fragments.admin_user_meta_row("User ID", user_id)
		)
		badge_html = html_fragments.admin_user_badge(role_label)
		actions = []
		if not is_anonymous:
			if role_label == "ADMIN":
				actions.append(html_fragments.admin_user_action_button("demote", user_id, "Demote to member", True))
			else:
				actions.append(html_fragments.admin_user_action_button("promote", user_id, "Promote to admin"))
			actions.append(html_fragments.admin_user_action_button("delete", user_id, "Delete user", True))
		actions_html = html_fragments.admin_user_actions("".join(actions))

		integration_cards: list[tuple[int, str]] = []
		try:
			webhooks, _ = interface.client.get_rows_with_filters(
				"discord_webhooks",
				equalities={"user_id": user_id},
				page_limit=50,
				page_num=0,
			)
			for wh in webhooks or []:
				subscriptions_html = ""
				try:
					sub_rows = interface.get_discord_webhook_subscriptions(wh.get("id"))
					if sub_rows:
						sub_cards = []
						for sub in sub_rows:
							perm = (sub.get("permission") or "unknown").upper()
							desc = sub.get("description") or ""
							created_at = sub.get("created_at")
							date_str = ""
							if created_at:
								try:
									date_str = created_at.strftime("%d %B %Y")
								except Exception:
									date_str = str(created_at)
							is_sub_active = bool(sub.get("is_active", True))
							status_label = "Active" if is_sub_active else "Inactive"
							sub_cards.append(
								html_fragments.subscription_card(
									sub.get("event_key") or "",
									perm,
									desc,
									date_str,
									status_label,
									is_sub_active,
									"",
									"",
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
				badge = html_fragments.integration_badge(status)
				if status == "Active":
					delete_button = html_fragments.integration_delete_action(
						"discord_webhook",
						str(wh.get("id")),
						"Discord Webhook",
						True,
						user_id=user_id,
						submit_route="/api/admin/users/integration/disable",
						active_label="Active",
					)
				else:
					delete_button = (
						html_fragments.integration_enable_action(
							"discord_webhook",
							str(wh.get("id")),
							"Discord Webhook",
							"Active",
							user_id=user_id,
							submit_route="/api/admin/users/integration/enable",
						)
						+ html_fragments.integration_delete_action(
							"discord_webhook",
							str(wh.get("id")),
							"Discord Webhook",
							False,
							user_id=user_id,
							submit_route="/api/admin/users/integration/disable",
							hidden=True,
						)
					)
				integration_cards.append((
					0 if status == "Active" else 1,
					html_fragments.integration_card(
						"discord_webhook",
						str(wh.get("id")),
						"Discord Webhook",
						html.escape(wh.get("name") or "Webhook"),
						html_fragments.secret_field(wh.get("webhook_url") or "", label="Webhook URL"),
						badge + delete_button,
						subscriptions_html,
					)
				))
		except Exception:
			pass

		try:
			whitelist_rows, _ = interface.client.get_rows_with_filters(
				"minecraft_whitelist",
				equalities={"user_id": user_id},
				page_limit=5,
				page_num=0,
			)
			for wl in whitelist_rows or []:
				joined = ""
				if wl.get("whitelisted_at"):
					try:
						joined = wl["whitelisted_at"].strftime("%d %B %Y")
					except Exception:
						joined = str(wl["whitelisted_at"])
				status = "Whitelisted" if wl.get("is_active", True) else "Suspended"
				delete_button = ""
				badge = html_fragments.integration_badge(status)
				if status == "Whitelisted":
					delete_button = html_fragments.integration_delete_action(
						"minecraft",
						str(wl.get("id")),
						"Minecraft",
						True,
						user_id=user_id,
						submit_route="/api/admin/users/integration/disable",
						active_label="Whitelisted",
					)
				else:
					delete_button = (
						html_fragments.integration_enable_action(
							"minecraft",
							str(wl.get("id")),
							"Minecraft",
							"Whitelisted",
							user_id=user_id,
							submit_route="/api/admin/users/integration/enable",
						)
						+ html_fragments.integration_delete_action(
							"minecraft",
							str(wl.get("id")),
							"Minecraft",
							False,
							user_id=user_id,
							submit_route="/api/admin/users/integration/disable",
							hidden=True,
						)
					)
				integration_cards.append((
					0 if status == "Whitelisted" else 1,
					html_fragments.integration_card(
						"minecraft",
						str(wl.get("id")),
						"Minecraft",
						f"Username: {html.escape(wl.get('mc_username') or '')}",
						f"Whitelisted {html.escape(joined) if joined else ''}",
						badge + delete_button,
					)
				))
		except Exception:
			pass

		try:
			abs_rows, _ = interface.client.get_rows_with_filters(
				"audiobookshelf_registrations",
				equalities={"user_id": user_id, "status": "approved"},
				page_limit=1,
				page_num=0,
			)
			if abs_rows:
				abs_row = abs_rows[0]
				approved_at = ""
				if abs_row.get("reviewed_at"):
					try:
						approved_at = abs_row["reviewed_at"].strftime("%d %B %Y")
					except Exception:
						approved_at = str(abs_row["reviewed_at"])
				status = "Approved" if abs_row.get("is_active", True) else "Suspended"
				delete_button = ""
				badge = html_fragments.integration_badge(status)
				if status == "Approved":
					delete_button = html_fragments.integration_delete_action(
						"audiobookshelf",
						str(abs_row.get("id")),
						"Audiobookshelf",
						True,
						user_id=user_id,
						submit_route="/api/admin/users/integration/disable",
						active_label="Approved",
					)
				else:
					delete_button = (
						html_fragments.integration_enable_action(
							"audiobookshelf",
							str(abs_row.get("id")),
							"Audiobookshelf",
							"Approved",
							user_id=user_id,
							submit_route="/api/admin/users/integration/enable",
						)
						+ html_fragments.integration_delete_action(
							"audiobookshelf",
							str(abs_row.get("id")),
							"Audiobookshelf",
							False,
							user_id=user_id,
							submit_route="/api/admin/users/integration/disable",
							hidden=True,
						)
					)
				integration_cards.append((
					0 if status == "Approved" else 1,
					html_fragments.integration_card(
						"audiobookshelf",
						str(abs_row.get("id")),
						"Audiobookshelf",
						html.escape(abs_row.get("email") or email),
						f"Approved {html.escape(approved_at) if approved_at else ''}",
						badge + delete_button,
					)
				))
		except Exception:
			pass

		if not integration_cards:
			display_name = (first + " " + last).strip() or "This user"
			integration_cards.append((
				0,
				html_fragments.integration_card_empty(
					f"{display_name} has not connected any services yet."
				),
			))
		integration_cards.sort(key=lambda item: item[0])
		integrations_html = html_fragments.admin_user_integrations(
			"".join(card for _, card in integration_cards)
		)

		cards_html.append(
			html_fragments.admin_user_card(
				user_id=user_id,
				name=(first + " " + last).strip() or "Unknown",
				email=email or "Unknown",
				meta_html=meta_html,
				badge_html=badge_html,
				actions_html=actions_html,
				integrations_html=integrations_html,
			)
		)

	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("User Management"),
			step_add_stylesheets("/static/css/profile.css", "/static/css/admin_users.css"),
			step_add_scripts("/static/js/admin_users.js", "/static/js/copy_tooltip.js"),
			step_admin_users_content("".join(cards_html)),
		),
	))

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
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Audiobookshelf Approvals"),
				step_add_stylesheets("/static/css/forms.css", "/static/css/centering.css"),
				step_add_scripts("/static/js/form_submit.js", "/static/js/admin_approvals.js"),
				step_admin_approvals_content("Audiobookshelf Approvals", ""),
			),
		))

	cards_html: list[str] = []
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
		cards_html.append(
			html_fragments.approval_card(
				html.escape(name),
				"Audiobookshelf Request",
				html.escape(status_label),
				rows_html,
				actions_html,
			)
		)

	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Audiobookshelf Approvals"),
			step_add_stylesheets("/static/css/forms.css", "/static/css/centering.css"),
			step_add_scripts("/static/js/form_submit.js", "/static/js/admin_approvals.js"),
			step_admin_approvals_content("Audiobookshelf Approvals", "".join(cards_html)),
		),
	))


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
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Discord Webhook Approvals"),
				step_add_stylesheets("/static/css/forms.css", "/static/css/centering.css"),
				step_add_scripts("/static/js/form_submit.js", "/static/js/admin_approvals.js"),
				step_admin_approvals_content("Discord Webhook Approvals", ""),
			),
		))

	cards_html: list[str] = []
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
		cards_html.append(
			html_fragments.approval_card(
				html.escape(r["name"]),
				f"Event key: {html.escape(r['event_key'])}",
				html.escape(status_label),
				rows_html,
				actions_html,
			)
		)

	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Discord Webhook Approvals"),
			step_add_stylesheets("/static/css/forms.css", "/static/css/centering.css"),
			step_add_scripts("/static/js/form_submit.js", "/static/js/admin_approvals.js"),
			step_admin_approvals_content("Discord Webhook Approvals", "".join(cards_html)),
		),
	))


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
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("Minecraft Approvals"),
				step_add_stylesheets("/static/css/forms.css", "/static/css/centering.css"),
				step_add_scripts("/static/js/admin_approvals.js"),
				step_admin_approvals_content("Minecraft Approvals", ""),
			),
		))

	cards_html: list[str] = []
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
		cards_html.append(
			html_fragments.approval_card(
				html.escape(r["mc_username"]),
				html.escape(r["who_are_you"]),
				html.escape(status_label),
				rows_html,
				actions_html,
			)
		)

	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Minecraft Approvals"),
			step_add_stylesheets("/static/css/forms.css", "/static/css/centering.css"),
			step_add_scripts("/static/js/admin_approvals.js"),
			step_admin_approvals_content("Minecraft Approvals", "".join(cards_html)),
		),
	))

def build_admin_api_access_approvals_page(user: dict | None) -> str:
	if not user:
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("API Access Approvals"),
				step_error_header(401, "Login required."),
				add_return_home,
			),
		))

	if not interface.is_admin(user.get("id")):
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("API Access Approvals"),
				step_error_header(403, "Admin access required."),
				add_return_home,
			),
		))

	rows, _ = interface.client.get_rows_with_filters(
		"api_access_registrations",
		equalities={"status": "pending"},
		page_limit=200,
		page_num=0,
		order_by="created_at",
		order_dir="DESC",
	)
	user_cache: dict[str, dict] = {}

	if not rows:
		return build_page(user, PageSpec(
			steps=(
				step_set_page_title("API Access Approvals"),
				step_add_stylesheets("/static/css/forms.css", "/static/css/centering.css"),
				step_add_scripts("/static/js/admin_approvals.js"),
				step_admin_approvals_content("API Access Approvals", ""),
			),
		))

	cards_html: list[str] = []
	for r in rows:
		status_label, _ = _get_user_status_label(r.get("user_id"), user_cache)
		submitted_at = ""
		if r.get("created_at"):
			try:
				submitted_at = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
			except Exception:
				submitted_at = str(r["created_at"])
		submitted_at = html.escape(submitted_at) if submitted_at else ""
		name = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip() or r.get("email", "Unknown")
		requested_scopes = r.get("requested_scopes") or []
		if isinstance(requested_scopes, list):
			scopes_text = ", ".join(str(s) for s in requested_scopes if s)
		else:
			scopes_text = str(requested_scopes)
		rows_html = (
			html_fragments.approval_row("Email", html.escape(r.get("email") or "—")) +
			html_fragments.approval_row("Principal", html.escape(r.get("principal_type") or "—")) +
			html_fragments.approval_row("Service", html.escape(r.get("service_name") or "—")) +
			html_fragments.approval_row("Submitted at", submitted_at or "—") +
			html_fragments.approval_row("Scopes", html.escape(scopes_text) if scopes_text else "—", full=True) +
			html_fragments.approval_row("Use case", html.escape(r.get("use_case") or "—"), full=True)
		)
		actions_html = html_fragments.approval_actions(
			"/api/admin/api-access/approve",
			"/api/admin/api-access/deny",
			str(r["id"]),
		)
		cards_html.append(
			html_fragments.approval_card(
				html.escape(name),
				"API Access Request",
				html.escape(status_label),
				rows_html,
				actions_html,
			)
		)

	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("API Access Approvals"),
			step_add_stylesheets("/static/css/forms.css", "/static/css/centering.css"),
			step_add_scripts("/static/js/admin_approvals.js"),
			step_admin_approvals_content("API Access Approvals", "".join(cards_html)),
		),
	))


def build_integration_remove_page(user: dict | None, token: str) -> str:
	page_html = html_fragments.center_column(
		html_fragments.heading("Remove Integration", 2)
		+ html_fragments.integration_remove_form(token)
	)
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Remove Integration"),
			step_add_stylesheets("/static/css/forms.css", "/static/css/centering.css"),
			step_add_scripts("/static/js/form_submit.js"),
			step_text_block(page_html),
		),
	))


def build_integration_removed_page(user: dict | None) -> str:
	page_html = html_fragments.center_column(
		html_fragments.heading("Integration Removed", 2)
		+ html_fragments.paragraph("Your integration has been removed successfully.")
		+ html_fragments.return_home()
	)
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Integration Removed"),
			step_add_stylesheets("/static/css/centering.css"),
			step_text_block(page_html),
		),
	))

def build_audiobookshelf_unavailable_page(user: dict | None, status_note: str | None = None) -> str:
	note = status_note or "The service did not respond."
	page_html = html_fragments.center_column(
		html_fragments.heading("Audiobookshelf is offline", 2)
		+ html_fragments.paragraph(
			"We could not reach the Audiobookshelf service on this machine."
		)
		+ html_fragments.paragraph(f"Status: {html.escape(note)}")
		+ html_fragments.return_home()
	)
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Audiobookshelf Offline"),
			step_add_stylesheets("/static/css/centering.css"),
			step_text_block(page_html),
		),
	))

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
