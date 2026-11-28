"""service-d: fault-injection demo upstream (slow and failing responses), FastAPI (ADR-001).

Used only to test gateway timeouts and upstream error handling.
It must never be exposed outside a test environment.
"""
import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from http import HTTPStatus

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

SERVICE_NAME = "service-d"

# Only accept request IDs that are short and made of harmless characters.
# Anything else is replaced, so a client cannot inject odd data into logs.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MAX_LOGGED_PATH = 200


def _get_logger():
    logger = logging.getLogger(SERVICE_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def _error(status, request_id, detail=None):
    """JSON error body shared by all error responses (same format as the Flask version)."""
    body = {"error": HTTPStatus(status).phrase, "status": status, "request_id": request_id}
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=status)


def create_app(max_body_bytes=None, max_slow_seconds=None):
    if max_body_bytes is None:
        max_body_bytes = int(os.environ.get("MAX_BODY_BYTES", "65536"))
    if max_slow_seconds is None:
        max_slow_seconds = float(os.environ.get("MAX_SLOW_SECONDS", "30"))
    logger = _get_logger()

    # docs_url=None: /docs is defined below with a relative link to openapi.json,
    # so Swagger UI also works through the gateway (/api/service-d/docs).
    app = FastAPI(title=SERVICE_NAME, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        start = time.perf_counter()
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id

        content_length = request.headers.get("Content-Length", "")
        if content_length.isdigit() and int(content_length) > max_body_bytes:
            response = _error(413, request_id)
        else:
            try:
                response = await call_next(request)
            except Exception as err:
                # Full details go to the server log only, never to the client.
                logger.error(json.dumps({"service": SERVICE_NAME, "event": "unhandled_exception", "type": type(err).__name__}))
                response = _error(500, request_id)

        response.headers["X-Request-ID"] = request_id
        # Log metadata only: never query string, headers, cookies or body.
        logger.info(
            json.dumps(
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "service": SERVICE_NAME,
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path[:_MAX_LOGGED_PATH],
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                }
            )
        )
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        response = _error(exc.status_code, request.state.request_id)
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    # FastAPI answers invalid input with 422 by default; the API contract uses 400.
    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        detail = f"{'.'.join(str(p) for p in first.get('loc', []))}: {first.get('msg', 'invalid input')}"
        return _error(400, request.state.request_id, detail)

    @app.get("/docs", include_in_schema=False)
    async def docs():
        return get_swagger_ui_html(openapi_url="openapi.json", title=f"{SERVICE_NAME} - docs")

    # HEAD is added explicitly: unlike Flask, FastAPI does not add it to GET routes.
    # include_in_schema=False keeps one operation per path in the OpenAPI docs. @app.get is the
    # inner decorator, so the GET route is registered first and a 405 lists GET in its Allow header.
    @app.head("/health", include_in_schema=False)
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": SERVICE_NAME}

    @app.head("/hello", include_in_schema=False)
    @app.get("/hello")
    async def hello(request: Request):
        return {"service": SERVICE_NAME, "message": "hello", "request_id": request.state.request_id}

    # allow_inf_nan=False: "nan" and "inf" are rejected (400) like in the Flask version.
    # asyncio.sleep does not block the worker, so other requests are still served meanwhile.
    @app.head("/slow", include_in_schema=False)
    @app.get("/slow")
    async def slow(request: Request, seconds: float = Query(1.0, ge=0, le=max_slow_seconds, allow_inf_nan=False)):
        await asyncio.sleep(seconds)
        return {"service": SERVICE_NAME, "slept_seconds": seconds, "request_id": request.state.request_id}

    # Only 5xx codes are allowed so this cannot imitate auth or routing errors.
    @app.head("/error", include_in_schema=False)
    @app.get("/error")
    async def error(request: Request, status: int = Query(500, ge=500, le=599)):
        return JSONResponse(
            {"error": "Injected failure", "status": status, "request_id": request.state.request_id},
            status_code=status,
        )

    return app
