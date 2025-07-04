from flask import request

page_titles = {
    "server" : "Server Overview",
    "forgot-password" : "Password Recovery Form",
    "reset-password" : "Reset Password",
    "verify" : "Email Verification",
    "profile" : "{username}'s Profile",
    "settings" : "Account Settings",
}

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
