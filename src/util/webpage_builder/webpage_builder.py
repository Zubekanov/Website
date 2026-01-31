from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sql.psql_interface import PSQLInterface

from util.webpage_builder import parent_builder
from util.webpage_builder.parent_builder import HTMLHelper
from util.webpage_builder.metrics_builder import METRICS_NAMES
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
		builder.add_html(f'<div class="{container_class}">\n\t<div class="{class_name}">\n')
		for s in contents:
			s(builder)
		builder.add_html("\t</div>\n</div>\n")
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

RETURN_HOME_HTML = "<p><a href='/'>Return to Home Page</a></p>\n"


def add_return_home(builder: PageBuilder):
	builder.add_html(RETURN_HOME_HTML)


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
	builder = PageBuilder(page_config=spec.page_config, navbar_config=spec.navbar_config, user=user)
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

def step_text_paragraph(text: str) -> Step:
	def _step(builder: PageBuilder):
		builder.add_html(f"<p>{text}</p>\n")
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

def step_form(
	*,
	form_id: str = "",
	class_name: str = "form",
	contents: tuple[Step, ...] = (),
) -> Step:
	def _step(builder: PageBuilder):
		id_attr = f' id="{form_id}"' if form_id else ""
		class_attr = f' class="{class_name}"' if class_name else ""

		builder._b.stylesheets.add("/static/css/forms.css")
		builder._b.scripts.add("/static/js/form_submit.js")

		builder.add_html(f"<form{id_attr}{class_attr}>\n")
		for s in contents:
			s(builder)
		builder.add_html("</form>\n")
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
		builder.add_html(
			f'<div class="{class_name}" '
			f'style="max-width:{max_width}; padding:{padding_y} {padding_x}; margin:0 auto;">\n'
		)
		for s in contents:
			s(builder)
		builder.add_html("</div>\n")
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

		style = f'style="border-radius:{rounding}; padding:{padding_y} {padding_x};"'

		if href:
			builder.add_html(f'<a class="{class_name} {class_name}--link" href="{href}" {style}>\n')
			for s in contents:
				s(builder)
			builder.add_html("</a>\n")
		else:
			builder.add_html(f'<div class="{class_name}" {style}>\n')
			for s in contents:
				s(builder)
			builder.add_html("</div>\n")
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

def build_profile_page(user: dict | None) -> str:
	user_name = f"{user['first_name']} {user['last_name']}"
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title(user_name + "'s Profile"),
			step_centering(
				contents=(
					step_heading(user_name + "'s Profile", 2),
					step_text_paragraph(f"Account created on {user['created_at'].strftime('%d %B %Y')}"),
					step_text_paragraph(f"<b>Email:</b> {user['email']}"),
					step_centered_box(
						href="/reset-password",
						contents=(
							step_heading("Change Password", 4),
						),
					),
					step_centered_box(
						href="/delete-account",
						contents=(
							step_heading("Delete Account", 4),
						),
					),
				),
			),
		),
	))


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


#TODO: Prefill fields if user is logged in
def build_audiobookshelf_registration_page(user: dict | None) -> str:
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Audiobookshelf Registration"),
			step_box(contents=(
				step_heading("Audiobookshelf Registration", 2),
				step_form(
					form_id="audiobookshelf-registration-form",
					class_name="form",
					contents=(
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
					),
				),
				step_text_block("<p>You will receive a follow-up email with further instructions if your registration is approved.</p>\n"),
			)),
		)
	))

def build_verify_email_page(user: dict | None) -> str:
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Verify Your Email"),
			step_box(contents=(
				step_heading("Verify Your Email", 2),
				step_text_block("<p>Thank you for registering! Please check your email for a verification link to complete your registration.</p>\n"),
			)),
		)
	))

def build_verify_email_token_page(user: dict | None, token: str) -> str:
	validation = interface.validate_verification_token(token)
	if validation:
		page_title = "Email Verified"
		message = "<p>Your email has been successfully verified! You can now log in to your account.</p>\n"
	else:
		page_title = "Invalid or Expired Token"
		message = "<p>The verification link is invalid or has expired. Please try registering again.</p>\n"
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
	return build_page(user, PageSpec(
		steps=(
			step_set_page_title("Server Metrics"),
			step_metrics_grid,
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
						step_text_paragraph(f"Please enter your password to confirm deletion of the account for <b>{user['email']}</b>:"),
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
			step_text_block("<p>The requested functionality is not yet implemented on this server.</p>\n"),
			add_return_home,
		),
	))
