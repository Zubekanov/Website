from util.webpage_builder import parent_builder

def _debug_prints():
    pass

def build_test_page():
    builder = parent_builder.WebPageBuilder()
    builder.load_page_config("homepage")
    builder._add_banner_html([
        "Item1",
        "Item2",
        "Item3",
    ], interval=4000, banner_type="ticker")
    html = builder.serve_html()
    return html
