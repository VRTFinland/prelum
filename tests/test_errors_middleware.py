import json
from http import HTTPStatus

from fastapi import Request
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import (
    AppError,
    ForbiddenError,
    InvalidTemplatePathError,
    Origin,
    RenderError,
    RenderTimeoutError,
    RequestTooLargeError,
    ServiceUnavailableError,
    StringTooLargeError,
    app_error_handler,
)
from app.main import create_app
from tests.conftest import TOKEN, client


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/test/path",
            "headers": [],
            "query_string": b"",
        }
    )


def test_app_error_has_correct_defaults():
    error = AppError("test message")
    assert error.status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert error.title == "Internal Server Error"
    assert error.detail == "test message"


def test_app_error_to_response():
    error = AppError("test error")
    response = error.to_response(_request())

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
    error = StringTooLargeError(size=5000, limit=1000)
    assert error.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert error.title == "String Too Large"
    assert error.detail == "String size 5000 bytes exceeds limit 1000"
    assert error.context == {"path": [], "subject": "value", "size": 5000, "limit": 1000}


def test_request_too_large_error_carries_the_limit():
    error = RequestTooLargeError(limit=1024)
    assert error.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert error.title == "Request Too Large"
    assert error.detail == "Request body exceeds 1024 bytes"
    assert error.context == {"limit": 1024}


def test_request_too_large_error_carries_a_declared_size_only_when_given():
    assert RequestTooLargeError(limit=1024, declared_size=5).context == {"limit": 1024, "declared_size": 5}
    assert "declared_size" not in RequestTooLargeError(limit=1024).context


def test_render_timeout_error():
    error = RenderTimeoutError(timeout_secs=15)
    assert error.status == HTTPStatus.REQUEST_TIMEOUT
    assert error.title == "Render Timeout"
    assert error.detail == "Render timed out after 15 seconds"
    assert error.context == {"timeout_secs": 15}


def test_app_error_handler_logs_and_returns_response():
    error = ForbiddenError("Test forbidden")

    response = app_error_handler(_request(), error)

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


def test_validation_error_returns_400_with_string_detail_and_structured_errors():
    response = client.post(
        "/v1/render",
        json={"invalid": "no source field"},
        headers=TOKEN,
    )
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "invalid_request"
    assert data["title"] == "Invalid Request"
    assert data["status"] == 400
    assert isinstance(data["detail"], str)
    assert data["detail"].startswith("source: ")
    assert response.headers["content-type"].startswith("application/problem+json")

    errors = data["context"]["errors"]
    assert {error["type"] for error in errors} == {"missing", "extra_forbidden"}
    for error in errors:
        assert set(error) == {"loc", "msg", "type"}
    assert ["body", "source"] in [error["loc"] for error in errors]


def test_app_error_context_defaults_to_an_empty_mapping():
    assert AppError("test").context == {}


def test_app_error_keeps_the_context_it_was_given():
    error = AppError("test", context={"limit": 10, "key": "a/b.png"})
    assert error.context == {"limit": 10, "key": "a/b.png"}


def test_to_response_always_carries_context_as_the_last_member():
    body = json.loads(AppError("test").to_response(_request()).body)
    assert set(body) == {"code", "title", "status", "detail", "instance", "origin", "context"}
    assert body["context"] == {}


def test_to_response_publishes_the_origin_as_a_plain_string():
    """Callers compare it to a literal, so it must serialise as "service", not as an enum repr."""
    body = json.loads(AppError("test").to_response(_request()).body)
    assert body["origin"] == "service"


def test_to_response_publishes_each_origin_verbatim():
    assert json.loads(RequestTooLargeError(limit=1).to_response(_request()).body)["origin"] == "request"
    assert json.loads(RenderError("boom").to_response(_request()).body)["origin"] == "service"


def test_app_error_defaults_to_the_service_origin():
    """The default is the loud one: a class that forgets to declare its origin pages us."""
    assert AppError("test").origin is Origin.service
    assert AppError("test").is_server_fault is True


def test_to_response_emits_context_values():
    error = AppError("test", context={"limit": 10})
    body = json.loads(error.to_response(_request()).body)
    assert body["context"] == {"limit": 10}
    assert body["detail"] == "test"


def test_detail_is_a_string_even_when_no_message_is_given():
    error = ForbiddenError()
    assert isinstance(error.detail, str)
    assert error.detail == "Forbidden"


def test_validation_errors_bound_caller_supplied_location_segments():
    """A key can fail Pydantic before validate_inline_file_key ever bounds it, so loc bounds it here.

    Both the prose and the published location would otherwise copy the key whole into the response
    body, the log line and Sentry — the leak for_message exists to prevent.
    """
    key = "k" * 5000
    response = client.post(
        "/v1/render",
        json={"source": '#text("hi")', "files": {key: {"encoding": "bogus", "content": "x"}}},
        headers=TOKEN,
    )

    assert response.status_code == 400
    data = response.json()
    assert key not in response.text
    assert len(data["detail"]) < 200
    (error,) = [error for error in data["context"]["errors"] if error["type"] == "literal_error"]
    assert error["loc"] == ["body", "files", "k" * 80 + "…", "encoding"]


def test_validation_error_locations_keep_array_indices_as_integers():
    """An index is Prelum's own count, not caller text, and a caller reads it as a position."""
    response = client.post(
        "/v1/render",
        json={"source": '#text("hi")', "output": {"format": "pdf", "standards": ["a-1b", "nonsense"]}},
        headers=TOKEN,
    )

    assert response.status_code == 400
    locations = [error["loc"] for error in response.json()["context"]["errors"]]
    assert any(1 in location for location in locations), locations


def test_validation_messages_are_bounded_too():
    """
    Pydantic quotes the offending value in some messages, so `msg` mirrors input as surely as `loc`.

    An invalid discriminator tag is embedded whole: an unbounded one comes back twice, in
    `context.errors[].msg` and in the `detail` built from it, turning a rejected request into
    response amplification and putting caller content through every intermediary that logs bodies.
    """
    marker = "M" * 6000
    response = client.post(
        "/v1/render",
        json={"source": "x", "output": {"format": marker}},
        headers=TOKEN,
    )

    assert response.status_code == 400
    assert marker not in response.text
    assert len(response.content) < 1000

    (error,) = [error for error in response.json()["context"]["errors"] if error["type"] == "union_tag_invalid"]
    assert error["msg"].endswith("…")


def test_a_legitimate_validation_message_survives_the_bound():
    """The bound exists for mirrored input, not to mangle the message a caller needs to read."""
    response = client.post(
        "/v1/render",
        json={"source": "x", "output": {"format": "gif"}},
        headers=TOKEN,
    )

    (error,) = [error for error in response.json()["context"]["errors"] if error["type"] == "union_tag_invalid"]
    assert "gif" in error["msg"]
    assert not error["msg"].endswith("…")


def _problem(response: object) -> dict[str, object]:
    body = response.json()  # pyright: ignore[reportAttributeAccessIssue]
    assert response.headers["content-type"].startswith("application/problem+json")  # pyright: ignore[reportAttributeAccessIssue]
    assert set(body) == {"code", "title", "status", "detail", "instance", "origin", "context"}
    return body


def test_an_unknown_route_is_a_problem_response():
    """Routing failures used to escape both handlers and answer in Starlette's own JSON shape."""
    response = client.get("/v1/no-such-route")

    assert response.status_code == 404
    assert _problem(response)["code"] == "not_found"


def test_a_wrong_method_is_a_problem_response_and_keeps_allow():
    response = client.get("/v1/render")

    assert response.status_code == 405
    assert _problem(response)["code"] == "method_not_allowed"
    # Starlette's own 405 carries Allow; normalising the body must not drop the header that tells
    # the caller what to do instead.
    assert "POST" in response.headers["allow"]


def test_a_body_that_no_json_parser_accepts_is_a_problem_response():
    """
    Python bounds decimal-to-integer conversion, so a long enough JSON number is not a JSONDecodeError.

    FastAPI answers that with its own HTTPException rather than a validation error, which bypassed
    the validation handler entirely and broke the promise that every failure is problem+json.
    """
    response = client.post(
        "/v1/render",
        content=b'{"source": 1' + b"0" * 5000 + b"}",
        headers={
            **TOKEN,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 400
    body = _problem(response)
    assert body["code"] == "invalid_request"
    # Nothing of the caller's body may come back, and an unparseable body produced no error list.
    assert body["context"] == {}


def test_an_excessive_files_mapping_is_refused_before_its_entries_are_validated():
    """
    The key-count cap used to sit in a model_validator(mode="after").

    Pydantic validates every nested RenderFile before an after-validator runs, so a mapping far past
    the cap was fully validated first — two errors per malformed entry, every one of them published.
    A 1 MB body came back as a 16 MB response, all outside the render semaphore.
    """
    files = {f"f{index}.txt": {"encoding": 1, "content": 2} for index in range(5000)}
    response = client.post("/v1/render", json={"source": "x", "files": files}, headers=TOKEN)

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "invalid_file_data"
    assert body["context"]["count"] == 5000
    # The response says the mapping is too large; it does not enumerate what is wrong inside it.
    assert len(response.content) < 2000


def test_a_long_validation_error_list_is_capped_and_says_so():
    """A mapping within the key cap can still carry hundreds of faults; the response must stay small."""
    files = {f"f{index}.txt": {"encoding": 1, "content": 2} for index in range(300)}
    response = client.post("/v1/render", json={"source": "x", "files": files}, headers=TOKEN)

    assert response.status_code == 400
    context = response.json()["context"]
    assert len(context["errors"]) == 20
    # Truncation the caller cannot detect is worse than truncation: the total says what was dropped.
    assert context["errors_total"] == 600


def test_a_short_validation_error_list_is_published_whole():
    """The cap exists for amplification, not to hide the two errors a caller actually needs."""
    response = client.post("/v1/render", json={"source": "x", "output": {"format": "gif"}}, headers=TOKEN)

    context = response.json()["context"]
    assert 0 < len(context["errors"]) < 20
    assert "errors_total" not in context


def test_a_rewritten_status_does_not_inherit_the_original_headers():
    """
    Only 404 and 405 keep their status; everything else is classified by status alone.

    Grafting the original exception's headers onto a rewritten status would answer 400 with, say, a
    WWW-Authenticate that belongs to the 401 that was raised. No route raises one today, which is
    exactly why the rule has to be in the handler rather than in the routes.
    """
    app = create_app()
    client_with_route = TestClient(app, raise_server_exceptions=False)

    @app.get("/test/conflict")
    async def _conflict() -> None:
        raise StarletteHTTPException(status_code=409, headers={"X-Conflict-Token": "abc"})

    response = client_with_route.get("/test/conflict")

    assert response.status_code == 400
    assert "x-conflict-token" not in response.headers
    assert response.headers["content-type"].startswith("application/problem+json")


def test_a_preserved_status_keeps_its_headers():
    """The 405 rule is the reason headers are copied at all: Allow says what to send instead."""
    response = client.get("/v1/render")

    assert response.status_code == 405
    assert "POST" in response.headers["allow"]
