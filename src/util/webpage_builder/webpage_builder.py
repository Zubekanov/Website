from util.webpage_builder import parent_builder
from util.webpage_builder.parent_builder import BUILD_MS
from util.webpage_builder.metrics_builder import METRICS_NAMES

def _debug_prints():
    pass

def build_test_page():
    builder = parent_builder.WebPageBuilder()
    builder.load_page_config("default")
    builder._add_banner_html([
        "New website just dropped!",
        "Using new server hardware as well.",
        "Server hardware: ODroid-H4 Ultra",
        "Core™ i3 Processor N305",
        "32 GB Ram",
        "2TB NVMe SSD",
        "2x 8TB HDD",
    ], banner_type="ticker", interval=4000)
    builder._build_nav_html("navbar_landing.json")
    builder._add_plotly_metric_graph_grid(list(METRICS_NAMES.keys()))
    for i in range(10):
        builder._add_main_content_html(f"<p>Paragraph {i+1}</p>\n")
    html = builder.serve_html()
    return html

def build_login_page():
    builder = parent_builder.WebPageBuilder()
    builder.load_page_config("default")
    builder._build_nav_html("navbar_landing.json")
    builder._add_login_window()
    html = builder.serve_html()
    return html

def build_register_page():
    builder = parent_builder.WebPageBuilder()
    builder.load_page_config("default")
    builder._build_nav_html("navbar_landing.json")
    builder._add_register_window()
    html = builder.serve_html()
    return html

def build_4xx_page(e):
    builder = parent_builder.WebPageBuilder()
    builder.load_page_config("default")
    builder._build_nav_html("navbar_landing.json")
    builder._add_main_content_html(f"<h1>Error {e.code}</h1><p>{e.description}</p>\n")
    builder._add_main_content_html("<p><a href='/'>Return to Home Page</a></p>\n")
    html = builder.serve_html()
    return html
