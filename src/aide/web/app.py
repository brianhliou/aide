"""Flask app factory — creates and configures the dashboard application."""

from __future__ import annotations

from flask import Flask

from aide.config import AideConfig


def create_app(config: AideConfig) -> Flask:
    """Create the Flask app with config values and registered routes.

    Args:
        config: AideConfig with db_path, subscription_user, etc.

    Returns:
        Configured Flask application.
    """
    app = Flask(__name__)

    app.config["DB_PATH"] = config.db_path
    app.config["SUBSCRIPTION_USER"] = config.subscription_user

    from aide.web.routes import bp

    app.register_blueprint(bp)

    @app.context_processor
    def inject_globals():
        return {"subscription_user": app.config["SUBSCRIPTION_USER"]}

    return app
