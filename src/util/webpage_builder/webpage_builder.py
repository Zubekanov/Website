from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from util.webpage_builder import parent_builder
from util.webpage_builder.parent_builder import HTMLHelper
from util.webpage_builder.metrics_builder import METRICS_NAMES
from util.fcr.file_config_reader import FileConfigReader

fcr = FileConfigReader()

class PageBuilder:
	def __init__(
		self,
		*,
		page_config: str = "default",
		navbar_config: str = "navbar_landing.json",
	):
		self._b = parent_builder.WebPageBuilder()
		self._b.load_page_config(page_config)
		self._b._build_nav_html(navbar_config)

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
	
def step_box_begin(
	class_name: str = "login-window",
	container_class: str = "login-container",
) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(
			f'<div class="{container_class}">\n'
			f'\t<div class="{class_name}">\n'
		)
		builder._b.stylesheets.add("/static/css/login.css")
	return _step

def step_box_end(builder: PageBuilder):
	builder.add_html("\t</div>\n</div>\n")

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

RETURN_HOME_HTML = "<p><a href='/'>Return to Home Page</a></p>\n"


def add_return_home(builder: PageBuilder):
	builder.add_html(RETURN_HOME_HTML)


Step = Callable[[PageBuilder], None]


@dataclass(frozen=True)
class PageSpec:
	page_config: str = "default"
	navbar_config: str = "navbar_landing.json"
	steps: tuple[Step, ...] = ()


def build_page(spec: PageSpec) -> str:
	builder = PageBuilder(page_config=spec.page_config, navbar_config=spec.navbar_config)
	for step in spec.steps:
		step(builder)
	return builder.render()


# ----------------------------
# Basic generic steps
# ----------------------------

def step_hardware_banner(builder: PageBuilder):
	builder.add_banner(HARDWARE_BANNER_LINES, banner_type="ticker", interval=4000)


def step_metrics_grid(builder: PageBuilder):
	builder.add_metric_graph_grid(list(METRICS_NAMES.keys()))


def step_lorem_ipsum(n: int = 1) -> Step:
	def _step(builder: PageBuilder):
		lorem_paragraphs = LOREM_IPSUM.split("\n\n")
		for i in range(n):
			paragraph = lorem_paragraphs[i % len(lorem_paragraphs)]
			builder.add_html(f"<p>{paragraph}</p>\n")
	return _step


def step_error_header(code: int, description: str) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(f"<h1>Error {code}</h1><p>{description}</p>\n")
	return _step


def step_text_block(html: str) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(html)
	return _step


def step_heading(text: str, level: int = 2) -> Step:
	def _step(builder: PageBuilder):
		lvl = min(max(int(level), 1), 6)
		builder.add_html(f"<h{lvl}>{text}</h{lvl}>\n")
	return _step


def step_link_paragraph(text: str, href: str) -> Step:
	def _step(builder: PageBuilder):
		link = HTMLHelper.link_string(text=text, href=href)
		builder.add_html(f"<p>{link}</p>\n")
	return _step

def step_set_page_title(title: str) -> Step:
	def _step(builder: PageBuilder):
		builder._b.set_page_title(title)
	return _step

# ----------------------------
# Form steps (HTMLHelper wired in)
# These do NOT implement auth; they only render fields + submit button.
# Your JS (form_submit.js) handles the click behaviour.
# ----------------------------

def step_form_begin(form_id: str = "", class_name: str = "form") -> Step:
	def _step(builder: PageBuilder):
		id_attr = f' id="{form_id}"' if form_id else ""
		class_attr = f' class="{class_name}"' if class_name else ""
		builder.add_html(f"<form{id_attr}{class_attr}>\n")
		builder._b.stylesheets.add("/static/css/forms.css")
		builder._b.scripts.add("/static/js/form_submit.js")
	return _step


def step_form_end(builder: PageBuilder):
	builder.add_html("</form>\n")


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
		if class_name:
			btn_html = btn_html.replace("<button ", f'<button class="{class_name}" ', 1)

		builder.add_html(HTMLHelper.form_group(btn_html, class_name=group_class))
	return _step


def step_form_message_area(
	attr: str = "data-form-message",
	class_name: str = "form-message",
	lines: int = 2,
) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(
			f'<div class="{class_name}" {attr} data-lines="{lines}" aria-live="polite"></div>\n'
		)
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


# ----------------------------
# Page builders
# ----------------------------

def build_test_page() -> str:
	return build_page(PageSpec(
		steps=(
			step_set_page_title("Test Page"),
			step_hardware_banner,
			step_metrics_grid,
			step_lorem_ipsum(2),
		),
	))

def build_login_page() -> str:
	return build_page(PageSpec(
		steps=(
			step_set_page_title("Login"),
			step_box_begin(),
			step_heading("Login", 2),

			step_form_begin("login-form", "form"),
			step_text_input_group("Email", "email", placeholder="Email"),
			step_password_input_group("Password", "password", placeholder="Password"),
			step_submit_button(
				"Log in",
				submission_fields=["email", "password"],
				submission_route="/login",
				submission_method="POST",
				success_redirect="/",
				failure_redirect=None,
			),
			step_form_message_area(),
			step_form_end,
			step_link_paragraph("Don't have an account? Register", "/register"),

			step_box_end,
		),
	))

def build_register_page() -> str:
	return build_page(PageSpec(
		steps=(
			step_set_page_title("Register"),
			step_box_begin(),
			step_heading("Register", 2),
			step_form_begin("register-form", "form"),
			step_dropdown_group(
                label="How did you discover my website?",
                name="referral_source",
                options = [
					("friend", "Friend or colleague"),
                    ("github", "GitHub"),
                    ("resume", "Resume / CV"),
                    ("linkedin", "LinkedIn or online profile"),
                    ("other", "Other"),
                ]
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
				success_redirect="pending-verification",
				failure_redirect=None,
			),
			step_form_message_area(),
			step_form_end,
			step_link_paragraph("Already have an account? Log in", "/login"),
            step_box_end,
		),
	))

#TODO: Prefill fields if user is logged in
def build_audiobookshelf_registration_page() -> str:
	return build_page(PageSpec(
		steps=(
			step_set_page_title("Audiobookshelf Registration"),
			step_box_begin(),
            step_heading("Audiobookshelf Registration", 2),
            step_form_begin("audiobookshelf-registration-form", "form"),
			step_text_input_group("First Name", "first_name", placeholder="First Name"),
            step_text_input_group("Last Name", "last_name", placeholder="Last Name"),
            step_text_input_group("Email", "email", placeholder="Email"),
			step_textarea_group("Additional Information", "additional_info", placeholder="Enter any additional information here..."),
            step_submit_button(
                "Submit Registration",
                submission_fields=["first_name", "last_name", "email", "additional_info"],
                submission_route="/audiobookshelf-registration",
                submission_method="POST",
                success_redirect="/",
				failure_redirect=None,
            ),
			step_form_message_area(),
            step_form_end,
			step_text_block("<p>You will receive a follow-up email with further instructions if your registration is approved.</p>\n"),
            step_box_end,
        )
    ))

def build_server_metrics_page() -> str:
	return build_page(PageSpec(
		steps=(
			step_set_page_title("Server Metrics"),
			step_metrics_grid,
			),
	))


def build_4xx_page(e) -> str:
	return build_page(PageSpec(
		steps=(
			step_set_page_title(f"{e.code} Error"),
			step_error_header(e.code, e.description),
			add_return_home,
		),
	))


def build_501_page() -> str:
	return build_page(PageSpec(
		steps=(
			step_set_page_title("501 Not Implemented"),
			step_error_header(501, "Not Implemented"),
			step_text_block("<p>The requested functionality is not yet implemented on this server.</p>\n"),
			add_return_home,
		),
	))
