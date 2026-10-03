"""Flask application factory and Stage 0 smoke endpoint."""

from flask import Flask


def create_app() -> Flask:
    """Create an application without external services or credentials."""
    app = Flask(__name__)

    @app.get("/")
    def index() -> dict[str, str]:
        """Confirm that the application is running."""
        return {"service": "web-monitor", "status": "ok"}

    return app
