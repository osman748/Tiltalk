"""TillTalk HTTP server.

POST/GET/DELETE /mcp   the MCP endpoint (Streamable HTTP, spec 2025-11-25)
GET  /                 the simulator UI
GET  /api/*            helper endpoints for the simulator (not part of MCP)
"""
import hmac
import json
import logging
import os
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, request, send_from_directory

import interpret as nlu
import ledger
import mcp

logging.basicConfig(level=logging.INFO)
HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

AUTH_TOKEN = os.environ.get("TILLTALK_TOKEN", "")
EXTRA_ORIGINS = {o.strip().rstrip("/") for o in os.environ.get("TILLTALK_ALLOWED_ORIGINS", "").split(",") if o.strip()}
READ_ONLY = os.environ.get("TILLTALK_READ_ONLY_DEMO", "") == "1"


def _origin_ok():
    """DNS-rebinding protection required by the MCP spec: a present Origin must be ours or allowed."""
    origin = request.headers.get("Origin")
    if not origin:
        return True
    if origin.rstrip("/") in EXTRA_ORIGINS:
        return True
    try:
        o = urlparse(origin)
    except ValueError:
        return False
    return bool(o.netloc) and o.netloc == request.host


def _authorised():
    if not AUTH_TOKEN:
        return True
    got = request.headers.get("Authorization", "")
    return got.startswith("Bearer ") and hmac.compare_digest(got[7:], AUTH_TOKEN)


def _shop():
    return ledger.clean_shop(request.headers.get("X-Shop") or request.args.get("shop"))


def _jsonrpc_http_error(status, code, message):
    body = {"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}}
    return Response(json.dumps(body), status=status, mimetype="application/json")


@app.after_request
def _headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if request.path == "/":
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    return resp


@app.route("/mcp", methods=["POST", "GET", "DELETE", "OPTIONS"])
def mcp_endpoint():
    if not _origin_ok():
        return _jsonrpc_http_error(403, -32000, "Forbidden: invalid Origin.")
    if request.method == "OPTIONS":
        return Response(status=204)
    if not _authorised():
        return Response(json.dumps({"error": "unauthorized"}), status=401, mimetype="application/json")
    if request.method in ("GET", "DELETE"):  # no server-initiated stream, no session to terminate
        return Response(status=405, headers={"Allow": "POST"})

    ver = request.headers.get("MCP-Protocol-Version")
    if ver and ver not in mcp.SUPPORTED_VERSIONS:
        return _jsonrpc_http_error(400, -32600, f"Unsupported MCP-Protocol-Version: {ver[:40]}")
    if request.mimetype != "application/json":
        return _jsonrpc_http_error(415, -32600, "Send Content-Type: application/json.")
    try:
        msg = json.loads(request.get_data(as_text=True) or "null")
    except (ValueError, UnicodeDecodeError):
        return _jsonrpc_http_error(400, -32700, "Parse error: body is not valid JSON.")
    if isinstance(msg, list):
        return _jsonrpc_http_error(400, -32600, "Batches are not supported. Send one message per request.")

    status, body = mcp.handle(msg, _shop())
    if body is None:
        return Response(status=202)
    return Response(json.dumps(body), status=status, mimetype="application/json")


# ------------------------------------------------------------ simulator helpers

@app.get("/")
def index():
    return send_from_directory(HERE, "index.html")


@app.get("/api/status")
def status():
    return jsonify({"currency": ledger.CURRENCY, "confirm_limit_minor": ledger.CONFIRM_LIMIT_MINOR,
                    "auth_required": bool(AUTH_TOKEN), "protocol": mcp.LATEST,
                    "read_only_demo": READ_ONLY, "today": ledger.today()})


@app.post("/api/interpret")
def api_interpret():
    """The Alexa+ stand-in: text in, a tool call (or a reply) out. The browser then calls /mcp itself."""
    if not _origin_ok():
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", ""))[:300]
    res = nlu.interpret(text, has_pending=bool(data.get("has_pending")))
    if res.get("arguments", {}).get("date") == "__yesterday__":
        res["arguments"]["date"] = ledger.yesterday()
    return jsonify(res)


@app.get("/api/ledger")
def api_ledger():
    if not _origin_ok():
        return jsonify({"error": "forbidden"}), 403
    shop = _shop()
    return jsonify({"summary": ledger.daily_summary(shop), "owes": ledger.who_owes_me(shop),
                    "recent": ledger.list_recent(shop, 8)})


@app.post("/api/demo/seed")
def api_seed():
    if not _origin_ok():
        return jsonify({"error": "forbidden"}), 403
    if READ_ONLY:
        return jsonify({"error": "disabled on this demo"}), 403
    try:
        return jsonify(ledger.seed_demo(_shop()))
    except ledger.LedgerError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/demo/reset")
def api_reset():
    if not _origin_ok():
        return jsonify({"error": "forbidden"}), 403
    if READ_ONLY:
        return jsonify({"error": "disabled on this demo"}), 403
    ledger.clear_shop(_shop())
    return jsonify({"status": "cleared"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port)
