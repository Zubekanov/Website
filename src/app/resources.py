import logging
import flask

logger = logging.getLogger(__name__)
resources = flask.Blueprint("resources", __name__)

@resources.route("/Joseph-Wong/resume")
def serve_resume():
    return flask.send_file(
        "static/resources/Joseph_Wong_Resume.pdf",
        mimetype="application/pdf",
        as_attachment=False
    )