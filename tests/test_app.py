"""Smoke test for the public HTTP endpoint."""

from web_monitor.app import create_app


def test_health_returns_service_status():
    """The application starts without credentials and serves JSON."""
    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.is_json
    assert response.get_json() == {"service": "web-monitor", "status": "ok"}
