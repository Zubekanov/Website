from flask import request
from util.config_reader import ConfigReader

page_titles = ConfigReader.get_key_value_config("breadcrumbs.config")

def generate_breadcrumbs(user=None):
    """Automatically generate breadcrumbs from the current request path."""
    parts = request.path.strip("/").split("/")
    breadcrumbs = []
    url_accumulator = ""

    if not parts or parts == ['']:
        return [{"name": "Home", "url": "/"}]

    breadcrumbs.append({"name": "Home", "url": "/"})
    for part in parts:
        url_accumulator += f"/{part}"
        title = page_titles.get(part, part.capitalize())
        if user:
            try:
                title = title.format(**user)
            except KeyError:
                pass
        breadcrumbs.append({"name": title, "url": url_accumulator})

    return breadcrumbs
