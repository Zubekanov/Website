from __future__ import annotations

from dataclasses import dataclass, field
import html
import json
from typing import Any, Iterable, Mapping, Sequence

import flask
from flask import has_request_context

from util.webpage_builder import parent_builder


def _escape(value: object) -> str:
	return html.escape("" if value is None else str(value), quote=True)


def _class_attr(class_name: str | None) -> str:
	if not class_name:
		return ""
	return f' class="{_escape(class_name)}"'


def _attrs_html(
	*,
	class_name: str | None = None,
	attrs: Mapping[str, object] | None = None,
	data_attrs: Mapping[str, object] | None = None,
) -> str:
	parts: list[str] = []
	if class_name:
		parts.append(f'class="{_escape(class_name)}"')

	for src, prefix in ((attrs or {}, ""), (data_attrs or {}, "data-")):
		for key, raw_value in src.items():
			if raw_value is None or raw_value is False:
				continue
			attr_name = f"{prefix}{key}"
			if raw_value is True:
				parts.append(attr_name)
				continue
			parts.append(f'{attr_name}="{_escape(raw_value)}"')

	if not parts:
		return ""
	return " " + " ".join(parts)


@dataclass(frozen=True)
class RenderedComponent:
	html: str = ""
	stylesheets: tuple[str, ...] = ()
	scripts: tuple[str, ...] = ()
	head_scripts: tuple[str, ...] = ()
	preconnect_html: tuple[str, ...] = ()
	boot_data: Mapping[str, Any] = field(default_factory=dict)

	@staticmethod
	def merge(*parts: RenderedComponent) -> RenderedComponent:
		html_chunks: list[str] = []
		stylesheets: set[str] = set()
		scripts: set[str] = set()
		head_scripts: set[str] = set()
		preconnect_html: set[str] = set()
		boot_data: dict[str, Any] = {}

		for part in parts:
			if not part:
				continue
			if part.html:
				html_chunks.append(part.html)
			stylesheets.update(path for path in part.stylesheets if path)
			scripts.update(path for path in part.scripts if path)
			head_scripts.update(path for path in part.head_scripts if path)
			preconnect_html.update(chunk for chunk in part.preconnect_html if chunk)
			boot_data.update(dict(part.boot_data))

		return RenderedComponent(
			html="".join(html_chunks),
			stylesheets=tuple(sorted(stylesheets)),
			scripts=tuple(sorted(scripts)),
			head_scripts=tuple(sorted(head_scripts)),
			preconnect_html=tuple(sorted(preconnect_html)),
			boot_data=boot_data,
		)


class Component:
	def render(self, ctx: PageContext) -> RenderedComponent:
		raise NotImplementedError


@dataclass(frozen=True)
class PageContext:
	user: dict | None = None
	request: flask.Request | None = None
	is_admin: bool = False
	interface: Any | None = None
	fcr: Any | None = None
	route_args: Mapping[str, Any] = field(default_factory=dict)

	@classmethod
	def current(
		cls,
		*,
		user: dict | None = None,
		interface: Any | None = None,
		fcr: Any | None = None,
		route_args: Mapping[str, Any] | None = None,
	) -> PageContext:
		request_obj = flask.request if has_request_context() else None
		is_admin = False
		if user and interface is not None:
			try:
				is_admin = bool(interface.is_admin(user.get("id")))
			except Exception:
				is_admin = False
		return cls(
			user=user,
			request=request_obj,
			is_admin=is_admin,
			interface=interface,
			fcr=fcr,
			route_args=dict(route_args or {}),
		)

	@property
	def query(self):
		if self.request is None:
			return {}
		return self.request.args

	def query_value(self, key: str, default: str = "") -> str:
		value = self.query.get(key, default)
		return "" if value is None else str(value)

	def route_value(self, key: str, default: Any = None) -> Any:
		return self.route_args.get(key, default)


def _render_children(children: Sequence[Component], ctx: PageContext) -> RenderedComponent:
	return RenderedComponent.merge(*(child.render(ctx) for child in children))


@dataclass(frozen=True)
class RawHtml(Component):
	html: str
	stylesheets: tuple[str, ...] = ()
	scripts: tuple[str, ...] = ()
	head_scripts: tuple[str, ...] = ()
	preconnect_html: tuple[str, ...] = ()
	boot_data: Mapping[str, Any] = field(default_factory=dict)

	def render(self, ctx: PageContext) -> RenderedComponent:
		_ = ctx
		return RenderedComponent(
			html=self.html,
			stylesheets=self.stylesheets,
			scripts=self.scripts,
			head_scripts=self.head_scripts,
			preconnect_html=self.preconnect_html,
			boot_data=self.boot_data,
		)


@dataclass(frozen=True)
class Heading(Component):
	text: str
	level: int = 2
	class_name: str = ""

	def render(self, ctx: PageContext) -> RenderedComponent:
		_ = ctx
		level = min(max(int(self.level), 1), 6)
		class_attr = _class_attr(self.class_name)
		return RenderedComponent(html=f"<h{level}{class_attr}>{html.escape(self.text)}</h{level}>\n")


@dataclass(frozen=True)
class Text(Component):
	text: str
	tag: str = "p"
	class_name: str = ""

	def render(self, ctx: PageContext) -> RenderedComponent:
		_ = ctx
		class_attr = _class_attr(self.class_name)
		return RenderedComponent(html=f"<{self.tag}{class_attr}>{html.escape(self.text)}</{self.tag}>\n")


@dataclass(frozen=True)
class Link(Component):
	text: str
	href: str
	class_name: str = ""
	wrap_in_paragraph: bool = False
	wrapper_class: str = "link-row"

	def render(self, ctx: PageContext) -> RenderedComponent:
		_ = ctx
		link = f'<a href="{_escape(self.href)}"{_class_attr(self.class_name)}>{html.escape(self.text)}</a>'
		if self.wrap_in_paragraph:
			return RenderedComponent(html=f'<p class="{_escape(self.wrapper_class)}">{link}</p>\n')
		return RenderedComponent(html=link)


@dataclass(frozen=True)
class Stack(Component):
	children: tuple[Component, ...] = ()
	tag: str = "div"
	class_name: str = ""
	attrs: Mapping[str, object] = field(default_factory=dict)
	data_attrs: Mapping[str, object] = field(default_factory=dict)

	def render(self, ctx: PageContext) -> RenderedComponent:
		rendered = _render_children(self.children, ctx)
		attr_html = _attrs_html(class_name=self.class_name, attrs=self.attrs, data_attrs=self.data_attrs)
		return RenderedComponent(
			html=f"<{self.tag}{attr_html}>{rendered.html}</{self.tag}>\n",
			stylesheets=rendered.stylesheets,
			scripts=rendered.scripts,
			head_scripts=rendered.head_scripts,
			preconnect_html=rendered.preconnect_html,
			boot_data=rendered.boot_data,
		)


@dataclass(frozen=True)
class Section(Component):
	children: tuple[Component, ...] = ()
	title: str | None = None
	title_level: int = 2
	class_name: str = ""
	attrs: Mapping[str, object] = field(default_factory=dict)
	data_attrs: Mapping[str, object] = field(default_factory=dict)

	def render(self, ctx: PageContext) -> RenderedComponent:
		rendered_children = list(self.children)
		if self.title:
			rendered_children.insert(0, Heading(self.title, level=self.title_level))
		stack = Stack(
			children=tuple(rendered_children),
			tag="section",
			class_name=self.class_name,
			attrs=self.attrs,
			data_attrs=self.data_attrs,
		)
		return stack.render(ctx)


@dataclass(frozen=True)
class Box(Component):
	children: tuple[Component, ...] = ()
	container_class: str = "login-container"
	class_name: str = "login-window"

	def render(self, ctx: PageContext) -> RenderedComponent:
		rendered = _render_children(self.children, ctx)
		return RenderedComponent(
			html=(
				f'<div class="{_escape(self.container_class)}">'
				f'<div class="{_escape(self.class_name)}">{rendered.html}</div>'
				"</div>\n"
			),
			stylesheets=tuple(sorted(set(rendered.stylesheets).union({"/static/css/login.css"}))),
			scripts=rendered.scripts,
			head_scripts=rendered.head_scripts,
			preconnect_html=rendered.preconnect_html,
			boot_data=rendered.boot_data,
		)


@dataclass(frozen=True)
class Card(Component):
	children: tuple[Component, ...] = ()
	tag: str = "article"
	class_name: str = "centered-box"
	attrs: Mapping[str, object] = field(default_factory=dict)
	data_attrs: Mapping[str, object] = field(default_factory=dict)

	def render(self, ctx: PageContext) -> RenderedComponent:
		rendered = _render_children(self.children, ctx)
		attr_html = _attrs_html(class_name=self.class_name, attrs=self.attrs, data_attrs=self.data_attrs)
		return RenderedComponent(
			html=f"<{self.tag}{attr_html}>{rendered.html}</{self.tag}>\n",
			stylesheets=rendered.stylesheets,
			scripts=rendered.scripts,
			head_scripts=rendered.head_scripts,
			preconnect_html=rendered.preconnect_html,
			boot_data=rendered.boot_data,
		)


@dataclass(frozen=True)
class Modal(Component):
	title: str
	body: tuple[Component, ...] = ()
	class_name: str = "page-modal"
	hidden: bool = True
	attrs: Mapping[str, object] = field(default_factory=dict)
	data_attrs: Mapping[str, object] = field(default_factory=dict)

	def render(self, ctx: PageContext) -> RenderedComponent:
		rendered = _render_children(self.body, ctx)
		attrs = dict(self.attrs)
		if self.hidden:
			attrs.setdefault("hidden", "hidden")
		attr_html = _attrs_html(class_name=self.class_name, attrs=attrs, data_attrs=self.data_attrs)
		return RenderedComponent(
			html=(
				f"<div{attr_html}>"
				'<div class="page-modal__backdrop"></div>'
				'<div class="page-modal__dialog" role="dialog" aria-modal="true">'
				f"<h2>{html.escape(self.title)}</h2>"
				f"{rendered.html}"
				"</div>"
				"</div>\n"
			),
			stylesheets=rendered.stylesheets,
			scripts=rendered.scripts,
			head_scripts=rendered.head_scripts,
			preconnect_html=rendered.preconnect_html,
			boot_data=rendered.boot_data,
		)


@dataclass(frozen=True)
class FormAction:
	route: str
	method: str = "POST"
	success_redirect: str | None = None
	failure_redirect: str | None = None
	refresh_on_success: bool = False
	refresh_on_failure: bool = False

	def root_data_attrs(self) -> dict[str, object]:
		data = {
			"form-submit-route": self.route,
			"form-submit-method": self.method.upper(),
		}
		if self.success_redirect is not None:
			data["form-success-redirect"] = self.success_redirect
		if self.failure_redirect is not None:
			data["form-failure-redirect"] = self.failure_redirect
		if self.refresh_on_success:
			data["form-success-refresh"] = "true"
		if self.refresh_on_failure:
			data["form-failure-refresh"] = "true"
		return data


class Field(Component):
	pass


@dataclass(frozen=True)
class HiddenField(Field):
	name: str
	value: str = ""

	def render(self, ctx: PageContext) -> RenderedComponent:
		_ = ctx
		return RenderedComponent(
			html=f'<input type="hidden" name="{_escape(self.name)}" value="{_escape(self.value)}">\n'
		)


@dataclass(frozen=True)
class TextField(Field):
	label: str
	name: str
	placeholder: str = ""
	value: str = ""
	input_type: str = "text"
	class_name: str = ""
	group_class: str = "form-group"
	input_id: str | None = None
	attrs: Mapping[str, object] = field(default_factory=dict)
	data_attrs: Mapping[str, object] = field(default_factory=dict)

	def render(self, ctx: PageContext) -> RenderedComponent:
		_ = ctx
		field_id = self.input_id or self.name
		input_attrs = _attrs_html(
			class_name=self.class_name,
			attrs={
				"id": field_id,
				"name": self.name,
				"type": self.input_type,
				"placeholder": self.placeholder,
				"value": self.value,
				**self.attrs,
			},
			data_attrs=self.data_attrs,
		)
		return RenderedComponent(
			html=(
				f'<div class="{_escape(self.group_class)}">'
				f'<label for="{_escape(field_id)}">{html.escape(self.label)}</label>\n'
				f"<input{input_attrs}>\n"
				"</div>\n"
			)
		)


@dataclass(frozen=True)
class PasswordField(Field):
	label: str
	name: str
	placeholder: str = ""
	value: str = ""
	class_name: str = ""
	group_class: str = "form-group"
	input_id: str | None = None
	attrs: Mapping[str, object] = field(default_factory=dict)

	def render(self, ctx: PageContext) -> RenderedComponent:
		return TextField(
			label=self.label,
			name=self.name,
			placeholder=self.placeholder,
			value=self.value,
			input_type="password",
			class_name=self.class_name,
			group_class=self.group_class,
			input_id=self.input_id,
			attrs=self.attrs,
			data_attrs={"hide-value": "true"},
		).render(ctx)


@dataclass(frozen=True)
class TextAreaField(Field):
	label: str
	name: str
	placeholder: str = ""
	value: str = ""
	rows: int = 6
	class_name: str = ""
	group_class: str = "form-group"
	input_id: str | None = None
	attrs: Mapping[str, object] = field(default_factory=dict)

	def render(self, ctx: PageContext) -> RenderedComponent:
		_ = ctx
		field_id = self.input_id or self.name
		attr_html = _attrs_html(
			class_name=self.class_name,
			attrs={
				"id": field_id,
				"name": self.name,
				"rows": self.rows,
				"placeholder": self.placeholder,
				**self.attrs,
			},
		)
		return RenderedComponent(
			html=(
				f'<div class="{_escape(self.group_class)}">'
				f'<label for="{_escape(field_id)}">{html.escape(self.label)}</label>\n'
				f"<textarea{attr_html}>{html.escape(self.value)}</textarea>\n"
				"</div>\n"
			)
		)


@dataclass(frozen=True)
class SelectField(Field):
	label: str
	name: str
	options: tuple[tuple[str, str], ...]
	selected: str = ""
	placeholder: str | None = None
	class_name: str = ""
	group_class: str = "form-group"
	input_id: str | None = None
	required: bool = False
	attrs: Mapping[str, object] = field(default_factory=dict)

	def render(self, ctx: PageContext) -> RenderedComponent:
		_ = ctx
		field_id = self.input_id or self.name
		options_html: list[str] = []
		if self.placeholder is not None:
			selected_attr = " selected" if not self.selected else ""
			options_html.append(
				f'<option value="" disabled{selected_attr}>{html.escape(self.placeholder)}</option>'
			)
		for value, text in self.options:
			selected_attr = " selected" if value == self.selected else ""
			options_html.append(
				f'<option value="{_escape(value)}"{selected_attr}>{html.escape(text)}</option>'
			)
		attr_html = _attrs_html(
			class_name=self.class_name,
			attrs={
				"id": field_id,
				"name": self.name,
				**({"required": True} if self.required else {}),
				**self.attrs,
			},
		)
		return RenderedComponent(
			html=(
				f'<div class="{_escape(self.group_class)}">'
				f'<label for="{_escape(field_id)}">{html.escape(self.label)}</label>\n'
				f"<select{attr_html}>{''.join(options_html)}</select>\n"
				"</div>\n"
			)
		)


@dataclass(frozen=True)
class CheckboxField(Field):
	label: str
	name: str
	checked: bool = False
	class_name: str = ""
	group_class: str = "form-group"
	attrs: Mapping[str, object] = field(default_factory=dict)

	def render(self, ctx: PageContext) -> RenderedComponent:
		_ = ctx
		attr_html = _attrs_html(
			class_name=self.class_name,
			attrs={
				"name": self.name,
				"type": "checkbox",
				**({"checked": True} if self.checked else {}),
				**self.attrs,
			},
		)
		return RenderedComponent(
			html=(
				f'<div class="{_escape(self.group_class)}">'
				'<label class="checkbox-row">'
				f'<span class="checkbox-label">{html.escape(self.label)}</span>'
				f"<input{attr_html}>"
				"</label>"
				"</div>\n"
			)
		)


@dataclass(frozen=True)
class CustomField(Field):
	inner_html: str
	group_class: str | None = None
	stylesheets: tuple[str, ...] = ()
	scripts: tuple[str, ...] = ()

	def render(self, ctx: PageContext) -> RenderedComponent:
		_ = ctx
		if self.group_class:
			html_out = f'<div class="{_escape(self.group_class)}">{self.inner_html}</div>\n'
		else:
			html_out = self.inner_html
		return RenderedComponent(
			html=html_out,
			stylesheets=self.stylesheets,
			scripts=self.scripts,
		)


@dataclass(frozen=True)
class FormMessageArea(Component):
	class_name: str = "form-message"
	lines: int = 1

	def render(self, ctx: PageContext) -> RenderedComponent:
		_ = ctx
		return RenderedComponent(
			html=(
				f'<div class="{_escape(self.class_name)}" data-form-message '
				f'data-lines="{int(self.lines)}" aria-live="polite" hidden></div>\n'
			)
		)


@dataclass(frozen=True)
class SubmitButton(Component):
	label: str
	class_name: str = "primary"
	button_type: str = "submit"
	attrs: Mapping[str, object] = field(default_factory=dict)
	data_attrs: Mapping[str, object] = field(default_factory=dict)

	def render(self, ctx: PageContext) -> RenderedComponent:
		_ = ctx
		attr_html = _attrs_html(
			class_name=self.class_name,
			attrs={"type": self.button_type, **self.attrs},
			data_attrs=self.data_attrs,
		)
		return RenderedComponent(html=f"<button{attr_html}>{html.escape(self.label)}</button>\n")


@dataclass(frozen=True)
class Form(Component):
	action: FormAction
	fields: tuple[Component, ...] = ()
	submit_buttons: tuple[Component, ...] = ()
	form_id: str = ""
	class_name: str = "form"
	message_area: FormMessageArea | None = field(default_factory=FormMessageArea)
	attrs: Mapping[str, object] = field(default_factory=dict)
	data_attrs: Mapping[str, object] = field(default_factory=dict)

	def render(self, ctx: PageContext) -> RenderedComponent:
		child_parts = list(self.fields)
		child_parts.extend(self.submit_buttons)
		if self.message_area is not None:
			child_parts.append(self.message_area)

		rendered = _render_children(tuple(child_parts), ctx)
		attrs = dict(self.attrs)
		if self.form_id:
			attrs["id"] = self.form_id

		data_attrs = dict(self.data_attrs)
		data_attrs.update(self.action.root_data_attrs())

		attr_html = _attrs_html(
			class_name=self.class_name,
			attrs=attrs,
			data_attrs=data_attrs,
		)
		return RenderedComponent(
			html=f"<form{attr_html}>{rendered.html}</form>\n",
			stylesheets=tuple(sorted(set(rendered.stylesheets).union({"/static/css/forms.css"}))),
			scripts=tuple(sorted(set(rendered.scripts).union({"/static/js/form_submit.js"}))),
			head_scripts=rendered.head_scripts,
			preconnect_html=rendered.preconnect_html,
			boot_data=rendered.boot_data,
		)


@dataclass(frozen=True)
class RemoteActionButton(Component):
	label: str
	route: str
	method: str = "POST"
	class_name: str = ""
	data_attrs: Mapping[str, object] = field(default_factory=dict)
	attrs: Mapping[str, object] = field(default_factory=dict)

	def render(self, ctx: PageContext) -> RenderedComponent:
		_ = ctx
		data_attrs = {
			"submit-route": self.route,
			"submit-method": self.method.upper(),
			**self.data_attrs,
		}
		attr_html = _attrs_html(
			class_name=self.class_name,
			attrs={"type": "button", **self.attrs},
			data_attrs=data_attrs,
		)
		return RenderedComponent(
			html=f"<button{attr_html}>{html.escape(self.label)}</button>\n",
			stylesheets=("/static/css/forms.css",),
			scripts=("/static/js/form_submit.js",),
		)


@dataclass(frozen=True)
class ConfirmActionModal(Component):
	title: str
	body_html: str
	class_name: str
	data_attrs: Mapping[str, object] = field(default_factory=dict)
	hidden: bool = True

	def render(self, ctx: PageContext) -> RenderedComponent:
		_ = ctx
		attrs = {"hidden": "hidden"} if self.hidden else {}
		attr_html = _attrs_html(class_name=self.class_name, attrs=attrs, data_attrs=self.data_attrs)
		return RenderedComponent(html=f"<div{attr_html}>{self.body_html}</div>\n")


@dataclass(frozen=True)
class Page:
	title: str
	children: tuple[Component, ...] = ()
	page_config: str = "default"
	navbar_config: str = "auto"
	meta_description: str | None = None
	body_class: str | None = None
	stylesheets: tuple[str, ...] = ()
	scripts: tuple[str, ...] = ()
	head_scripts: tuple[str, ...] = ()
	config_values: Mapping[str, object] = field(default_factory=dict)
	include_navbar: bool = True
	include_default_footer: bool = True

	def render(self, ctx: PageContext) -> str:
		builder = parent_builder.WebPageBuilder()
		builder.load_page_config(self.page_config)
		if not self.include_default_footer:
			builder._remove_default_footer()
		builder.set_page_title(self.title)
		if self.meta_description:
			builder.config_values["meta_description"] = self.meta_description
		if self.body_class:
			builder.config_values["body_class"] = self.body_class
		for key, value in self.config_values.items():
			builder.config_values[key] = "" if value is None else str(value)

		if self.include_navbar:
			nav_config = self._resolve_navbar_config()
			if nav_config:
				builder._build_nav_html(nav_config, user=ctx.user, is_admin=ctx.is_admin)

		rendered = _render_children(self.children, ctx)
		builder.config_values["body_html"] = rendered.html
		builder.stylesheets.update(self.stylesheets)
		builder.stylesheets.update(rendered.stylesheets)
		builder.scripts.update(self.scripts)
		builder.scripts.update(rendered.scripts)

		all_head_scripts = set(self.head_scripts).union(rendered.head_scripts)
		if all_head_scripts:
			builder.config_values["scripts_head_html"] = "\n".join(
				f'<script src="{_escape(path)}"></script>' for path in sorted(all_head_scripts)
			)

		preconnect_html = set(rendered.preconnect_html)
		if preconnect_html:
			builder.config_values["preconnect_html"] = "\n".join(sorted(preconnect_html))

		if rendered.boot_data:
			builder.config_values["boot_json"] = json.dumps(rendered.boot_data, separators=(",", ":"))

		return builder.serve_html()

	def _resolve_navbar_config(self) -> str:
		if not self.navbar_config or self.navbar_config == "auto":
			return "navbar_landing.json"
		if self.navbar_config == "navbar_landing_admin.json":
			return "navbar_landing.json"
		return self.navbar_config


__all__ = [
	"Box",
	"Card",
	"CheckboxField",
	"Component",
	"ConfirmActionModal",
	"CustomField",
	"Field",
	"Form",
	"FormAction",
	"FormMessageArea",
	"Heading",
	"HiddenField",
	"Link",
	"Modal",
	"Page",
	"PageContext",
	"PasswordField",
	"RawHtml",
	"RemoteActionButton",
	"RenderedComponent",
	"Section",
	"SelectField",
	"Stack",
	"SubmitButton",
	"Text",
	"TextAreaField",
	"TextField",
]
