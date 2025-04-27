from flask import request

def generate_breadcrumbs():
    """Automatically generate breadcrumbs from the current request path."""
    parts = request.path.strip("/").split("/")
    breadcrumbs = []
    url_accumulator = ""

    if not parts or parts == ['']:
        # Homepage
        return [{"name": "Home", "url": "/"}]

    breadcrumbs.append({"name": "Home", "url": "/"})
    for part in parts:
        url_accumulator += f"/{part}"
        breadcrumbs.append({"name": part.capitalize(), "url": url_accumulator})
    
    return breadcrumbs
