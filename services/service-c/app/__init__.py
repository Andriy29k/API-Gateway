"""service-c: demo upstream with a small read-only catalog (FastAPI, ADR-001)."""
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

SERVICE_NAME = "service-c"

# Only accept request IDs that are short and made of harmless characters.
# Anything else is replaced, so a client cannot inject odd data into logs.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MAX_LOGGED_PATH = 200

# Static, stateless demo data (no real or personal data).
ITEMS = [
    {"id": 1, "name": "keyboard", "price": 49.9},
    {"id": 2, "name": "mouse", "price": 19.5},
    {"id": 3, "name": "monitor", "price": 189.0},
    {"id": 4, "name": "headset", "price": 79.0},
    {"id": 5, "name": "webcam", "price": 59.0},
]


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


def create_app(max_body_bytes=None):
    if max_body_bytes is None:
        max_body_bytes = int(os.environ.get("MAX_BODY_BYTES", "65536"))
    logger = _get_logger()

    # docs_url=None: /docs is defined below with a relative link to openapi.json,
    # so Swagger UI also works through the gateway (/api/service-c/docs).
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

    # Invalid limit (not an integer, < 1 or > 100) -> validation error -> 400.
    @app.head("/items", include_in_schema=False)
    @app.get("/items")
    async def list_items(request: Request, limit: int = Query(10, ge=1, le=100), q: str = ""):
        query = q.strip().lower()[:50]
        found = [item for item in ITEMS if query in item["name"]] if query else ITEMS
        return {
            "service": SERVICE_NAME,
            "count": len(found[:limit]),
            "items": found[:limit],
            "request_id": request.state.request_id,
        }

    # {item_id:int} only matches digits, so /items/abc is an unknown route (404) like in Flask,
    # not a validation error (400).
    @app.head("/items/{item_id:int}", include_in_schema=False)
    @app.get("/items/{item_id:int}")
    async def get_item(request: Request, item_id: int):
        for item in ITEMS:
            if item["id"] == item_id:
                return {"service": SERVICE_NAME, "item": item, "request_id": request.state.request_id}
        return _error(404, request.state.request_id)

    return app
