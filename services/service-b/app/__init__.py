"""service-b: demo upstream used to verify method, header and body forwarding (FastAPI, ADR-001)."""
import json
import logging
import os
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from http import HTTPStatus

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from prometheus_client import CollectorRegistry, Counter, Histogram, start_http_server
from starlette.exceptions import HTTPException as StarletteHTTPException

SERVICE_NAME = "service-b"

# Only accept request IDs that are short and made of harmless characters.
# Anything else is replaced, so a client cannot inject odd data into logs.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MAX_LOGGED_PATH = 200
# Other methods are counted as "OTHER", so a client cannot create new metric series.
_KNOWN_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}

# Headers that /inspect may return. Only an explicit list, so secrets such as
# Authorization or Cookie are never reflected back to the client.
_INSPECT_HEADERS = (
    "Host",
    "User-Agent",
    "Content-Type",
    "X-Request-ID",
    "X-Real-IP",
    "X-Forwarded-For",
    "X-Forwarded-Proto",
    "X-Custom-Header",
)
_MAX_HEADER_VALUE = 200


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


def _is_json(content_type):
    """Same rule as Flask's request.is_json: application/json or application/*+json."""
    mimetype = content_type.split(";")[0].strip().lower()
    return mimetype == "application/json" or (mimetype.startswith("application/") and mimetype.endswith("+json"))


def _create_metrics(registry):
    """Request metrics. "route" is the route template (e.g. /items/{item_id:int}), never the raw path,
    so the number of time series stays small."""
    requests = Counter(
        "http_requests_total", "HTTP requests handled by the service", ["method", "route", "status"], registry=registry
    )
    duration = Histogram(
        "http_request_duration_seconds", "Time to handle a request in the service", ["method", "route"], registry=registry
    )
    return requests, duration


def create_app(max_body_bytes=None, metrics_port=None):
    if max_body_bytes is None:
        max_body_bytes = int(os.environ.get("MAX_BODY_BYTES", "65536"))
    if metrics_port is None:
        metrics_port = os.environ.get("METRICS_PORT", "")
    logger = _get_logger()
    registry = CollectorRegistry()
    requests_total, request_duration = _create_metrics(registry)

    @asynccontextmanager
    async def lifespan(app):
        # Metrics are served on their own port (not 8000), so the gateway never exposes /metrics.
        # Needs one uvicorn worker per container: a second worker could not bind the same port.
        server = None
        if metrics_port:
            server, _ = start_http_server(int(metrics_port), registry=registry)
        yield
        if server:
            server.shutdown()
            server.server_close()

    # docs_url=None: /docs is defined below with a relative link to openapi.json,
    # so Swagger UI also works through the gateway (/api/service-b/docs).
    app = FastAPI(title=SERVICE_NAME, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.metrics_registry = registry

    async def read_body(request: Request):
        """Read the body; also limits requests without Content-Length (chunked)."""
        body = await request.body()
        if len(body) > max_body_bytes:
            raise HTTPException(status_code=413)
        return body

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
        duration = time.perf_counter() - start
        route = getattr(request.scope.get("route"), "path", "unmatched")
        method = request.method if request.method in _KNOWN_METHODS else "OTHER"
        requests_total.labels(method, route, str(response.status_code)).inc()
        request_duration.labels(method, route).observe(duration)
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
                    "duration_ms": round(duration * 1000, 2),
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

    @app.post("/echo")
    async def echo(request: Request):
        request_id = request.state.request_id
        if not _is_json(request.headers.get("Content-Type", "")):
            return _error(415, request_id, "Content-Type must be application/json")
        body = await read_body(request)
        try:
            payload = json.loads(body)
        except ValueError:  # also covers UnicodeDecodeError
            payload = None
        if payload is None:
            return _error(400, request_id, "Body is not valid JSON")
        return {
            "service": SERVICE_NAME,
            "received": payload,
            "content_length": len(body),
            "request_id": request_id,
        }

    # One decorator per method, so every method gets its own OpenAPI operation.
    @app.head("/inspect", include_in_schema=False)
    @app.delete("/inspect")
    @app.patch("/inspect")
    @app.put("/inspect")
    @app.post("/inspect")
    @app.get("/inspect")
    async def inspect(request: Request):
        """Describe the received request, so tests can verify what the gateway forwards."""
        headers = {
            name: request.headers[name][:_MAX_HEADER_VALUE]
            for name in _INSPECT_HEADERS
            if name in request.headers
        }
        return {
            "service": SERVICE_NAME,
            "method": request.method,
            "headers": headers,
            "body_bytes": len(await read_body(request)),
            "request_id": request.state.request_id,
        }

    return app
