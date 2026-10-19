"""service-b: demo upstream with GET and POST, used to verify body forwarding."""
import json
import logging
import os
import re
import sys
import time
import uuid

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

SERVICE_NAME = "service-b"

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


def create_app(config=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_BODY_BYTES", "65536"))
    if config:
        app.config.update(config)
    logger = _get_logger()

    @app.before_request
    def _start_request():
        g.start = time.perf_counter()
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex

    @app.after_request
    def _finish_request(response):
        response.headers["X-Request-ID"] = g.get("request_id", "-")
        duration_ms = round((time.perf_counter() - g.get("start", time.perf_counter())) * 1000, 2)
        # Log metadata only: never query string, headers, cookies or body.
        logger.info(
            json.dumps(
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "service": SERVICE_NAME,
                    "request_id": g.get("request_id", "-"),
                    "method": request.method,
                    "path": request.path[:_MAX_LOGGED_PATH],
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                }
            )
        )
        return response

    @app.errorhandler(HTTPException)
    def _http_error(err):
        return (
            jsonify(
                {
                    "error": err.name,
                    "status": err.code,
                    "request_id": g.get("request_id", "-"),
                }
            ),
            err.code,
        )

    @app.errorhandler(Exception)
    def _unexpected_error(err):
        # Full details go to the server log only, never to the client.
        logger.error(json.dumps({"service": SERVICE_NAME, "event": "unhandled_exception", "type": type(err).__name__}))
        return (
            jsonify({"error": "Internal Server Error", "status": 500, "request_id": g.get("request_id", "-")}),
            500,
        )

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": SERVICE_NAME})

    @app.get("/hello")
    def hello():
        return jsonify({"service": SERVICE_NAME, "message": "hello", "request_id": g.request_id})

    @app.post("/echo")
    def echo():
        if not request.is_json:
            return (
                jsonify(
                    {
                        "error": "Unsupported Media Type",
                        "status": 415,
                        "detail": "Content-Type must be application/json",
                        "request_id": g.request_id,
                    }
                ),
                415,
            )
        payload = request.get_json(silent=True)
        if payload is None:
            return (
                jsonify(
                    {
                        "error": "Bad Request",
                        "status": 400,
                        "detail": "Body is not valid JSON",
                        "request_id": g.request_id,
                    }
                ),
                400,
            )
        return jsonify(
            {
                "service": SERVICE_NAME,
                "received": payload,
                "content_length": request.content_length,
                "request_id": g.request_id,
            }
        )

    return app
