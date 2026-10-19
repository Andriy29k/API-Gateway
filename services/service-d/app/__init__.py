"""service-d: fault-injection demo upstream (slow and failing responses).

Used only to test gateway timeouts and upstream error handling.
It must never be exposed outside a test environment.
"""
import json
import logging
import os
import re
import sys
import time
import uuid

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

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


def _bad_request(detail):
    return (
        jsonify({"error": "Bad Request", "status": 400, "detail": detail, "request_id": g.request_id}),
        400,
    )


def create_app(config=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_BODY_BYTES", "65536"))
    app.config["MAX_SLOW_SECONDS"] = float(os.environ.get("MAX_SLOW_SECONDS", "30"))
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

    @app.get("/slow")
    def slow():
        raw = request.args.get("seconds", "1")
        try:
            seconds = float(raw)
        except ValueError:
            return _bad_request("seconds must be a number")
        if not 0 <= seconds <= app.config["MAX_SLOW_SECONDS"]:
            return _bad_request(f"seconds must be between 0 and {app.config['MAX_SLOW_SECONDS']:g}")
        time.sleep(seconds)
        return jsonify({"service": SERVICE_NAME, "slept_seconds": seconds, "request_id": g.request_id})

    @app.get("/error")
    def error():
        raw = request.args.get("status", "500")
        # Only 5xx codes are allowed so this cannot imitate auth or routing errors.
        if not raw.isdigit() or not 500 <= int(raw) <= 599:
            return _bad_request("status must be an integer between 500 and 599")
        status = int(raw)
        return (
            jsonify({"error": "Injected failure", "status": status, "request_id": g.request_id}),
            status,
        )

    return app
