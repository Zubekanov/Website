from util.webpage_builder import parent_builder

def _debug_prints():
    pass

def build_landing_page():
    builder = parent_builder.WebPageBuilder()
    builder.load_page_config("homepage")
    html = builder.serve_html()
    return html