import mimetypes
import os

from flask import Flask

from s3_web_browser.routes import register_routes

mimetypes.add_type("font/woff2", ".woff2")


def create_app(config_class: str = "config.Config") -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    app.jinja_env.trim_blocks = app.jinja_env.lstrip_blocks = True
    register_routes(app)

    return app
