from http import HTTPStatus

from fastapi import Request
from fastapi.testclient import TestClient

from app.core.errors import (
    AppError,
    ForbiddenError,
    InvalidTemplatePathError,
    RenderError,
    RenderTimeoutError,
    RequestTooLargeError,
    ServiceUnavailableError,
    StringTooLargeError,
    app_error_handler,
)
from app.main import app

client = TestClient(app)


def test_app_error_has_correct_defaults():
    error = AppError("test message")
    assert error.status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert error.title == "Internal Server Error"
    assert error.detail == "test message"


def test_app_error_to_response():
    from fastapi import Request

    error = AppError("test error")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/test/path",
            "headers": [],
            "query_string": b"",
        }
    )
    response = error.to_response(request)

    assert response.status_code == 500
    body = response.body.decode()
    assert "Internal Server Error" in body
    assert "test error" in body


def test_invalid_template_path_error():
    error = InvalidTemplatePathError("Bad path")
    assert error.status == HTTPStatus.BAD_REQUEST
    assert error.title == "Invalid Template Path"


def test_render_failed_error():
    error = RenderError("Rendering failed")
    assert error.status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert error.title == "Render Failed"


def test_service_unavailable_error():
    error = ServiceUnavailableError("Service down")
    assert error.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert error.title == "Service Unavailable"


def test_forbidden_error():
    error = ForbiddenError("Access denied")
    assert error.status == HTTPStatus.FORBIDDEN
    assert error.title == "Forbidden"


def test_string_too_large_error():
    error = StringTooLargeError(size=5000, max_allowed=1000)
    assert error.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert error.title == "String Too Large"
    assert "5000" in str(error.detail)


def test_request_too_large_error():
    error = RequestTooLargeError("Request body too large")
    assert error.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert error.title == "Request Too Large"


def test_render_timeout_error():
    error = RenderTimeoutError("Render timeout")
    assert error.status == HTTPStatus.REQUEST_TIMEOUT
    assert error.title == "Render Timeout"


def test_app_error_handler_logs_and_returns_response():
    error = ForbiddenError("Test forbidden")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/test/api",
            "headers": [],
            "query_string": b"",
        }
    )

    response = app_error_handler(request, error)

    assert response.status_code == 403
    body = response.body.decode()
    assert "Forbidden" in body
    assert "Test forbidden" in body


def test_request_id_middleware_adds_header():
    response = client.get("/health")
    assert "x-request-id" in response.headers


def test_request_id_middleware_preserves_existing_id():
    response = client.get("/health", headers={"x-request-id": "custom-id-123"})
    assert response.headers["x-request-id"] == "custom-id-123"


def test_validation_error_returns_400():
    response = client.post(
        "/v1/render",
        json={"invalid": "no source field"},
        headers={"X-Prelum-Api-Token": "dev-only-insecure-token"},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "invalid_request"
    assert data["title"] == "Invalid Request"
    assert data["status"] == 400
    assert "detail" in data
    assert response.headers["content-type"].startswith("application/problem+json")
