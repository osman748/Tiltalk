"""A small, spec-following MCP server core (JSON-RPC 2.0 over Streamable HTTP).

Written by hand with no SDK so every rule is visible. Implements: initialize (version
negotiation), notifications, ping, tools/list and tools/call for MCP spec 2025-11-25
(and falls back gracefully to 2025-06-18 / 2025-03-26 clients).
"""
import logging

import ledger

log = logging.getLogger("tilltalk.mcp")

SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26"]
LATEST = SUPPORTED_VERSIONS[0]
SERVER_INFO = {"name": "tilltalk", "title": "TillTalk", "version": "1.0.0"}
INSTRUCTIONS = (
    "TillTalk is a voice ledger for a small shop. Use the tools to record sales, expenses and customer payments, "
    "and to answer questions about the day and who owes money. Amounts are in " + ledger.CURRENCY + ". "
    "Read the 'text' of each tool result back to the shop owner. If a result says it needs confirmation, ask the "
    "owner to confirm and then call the confirm tool. Never guess a missing price or customer name: ask for it."
)


def _tool(name, title, description, props, required=None, read_only=False, destructive=False, idempotent=False):
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": {"type": "object", "properties": props, "required": required or [], "additionalProperties": False},
        "annotations": {"title": title, "readOnlyHint": read_only, "destructiveHint": destructive,
                        "idempotentHint": idempotent, "openWorldHint": False},
    }


TOOLS = [
    _tool("log_sale", "Log a sale",
          "Record that the shop owner sold something. Give the item, the quantity, and exactly one of unit_price "
          "(price each) or total_price (price for the whole sale). If the customer has not paid in full, set "
          "amount_paid (0 for a sale on credit) and give the customer's name so the debt can be tracked.",
          {"item": {"type": "string", "description": "What was sold, e.g. 'rice'."},
           "quantity": {"type": "number", "description": "How many were sold."},
           "unit_price": {"type": "number", "description": "Price of one item."},
           "total_price": {"type": "number", "description": "Price for the whole sale."},
           "amount_paid": {"type": "number", "description": "Amount paid so far. Omit if paid in full; 0 for credit."},
           "customer": {"type": "string", "description": "Customer name. Required if not paid in full."},
           "note": {"type": "string", "description": "Optional note, e.g. 'pays on Friday'."}},
          ["item", "quantity"]),
    _tool("log_expense", "Log an expense",
          "Record money the shop owner spent, such as transport, stock or bills.",
          {"description": {"type": "string", "description": "What the money was spent on."},
           "amount": {"type": "number", "description": "How much was spent."},
           "category": {"type": "string", "description": "Optional category, e.g. 'bills'."}},
          ["description", "amount"]),
    _tool("record_payment", "Record a customer payment",
          "Record that a customer paid back some of what they owe. The payment cannot be more than they owe.",
          {"customer": {"type": "string", "description": "Who paid."},
           "amount": {"type": "number", "description": "How much they paid."}},
          ["customer", "amount"]),
    _tool("who_owes_me", "Who owes me",
          "List the customers who owe money and how much, or check one customer's balance.",
          {"customer": {"type": "string", "description": "Optional: check just this customer."}},
          read_only=True, idempotent=True),
    _tool("daily_summary", "Daily summary",
          "Summarise a day: sales, expenses, profit, money on credit, best seller and total owed.",
          {"date": {"type": "string", "description": "Optional date as YYYY-MM-DD. Defaults to today."}},
          read_only=True, idempotent=True),
    _tool("list_recent", "Recent entries",
          "Read back the latest entries so the owner can check them.",
          {"limit": {"type": "integer", "description": "How many entries, 1 to 20. Defaults to 5."}},
          read_only=True, idempotent=True),
    _tool("undo_last", "Undo the last entry",
          "Remove the most recent entry, for example after a mistake. Only the latest entry can be undone.",
          {}, destructive=True),
    _tool("confirm", "Confirm or cancel",
          "Confirm (or cancel) a large entry that was waiting for the owner's yes. With no token it applies "
          "to the most recent one.",
          {"token": {"type": "string", "description": "Optional confirmation token from the earlier result."},
           "approve": {"type": "boolean", "description": "true to record it, false to cancel. Defaults to true."}}),
]
TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}

HANDLERS = {
    "log_sale": ledger.log_sale,
    "log_expense": ledger.log_expense,
    "record_payment": ledger.record_payment,
    "who_owes_me": ledger.who_owes_me,
    "daily_summary": ledger.daily_summary,
    "list_recent": ledger.list_recent,
    "undo_last": lambda shop: ledger.undo_last(shop),
    "confirm": ledger.confirm,
}


def _numeric(v):
    try:
        float(v.strip())
        return True
    except (ValueError, AttributeError):
        return False


TYPE_OK = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: (isinstance(v, (int, float)) and not isinstance(v, bool)) or (isinstance(v, str) and _numeric(v)),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
}


def validate_arguments(schema, args):
    """Return an error string, or None. Small on purpose: required keys, known keys, basic types."""
    if not isinstance(args, dict):
        return "arguments must be an object."
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in args or args[key] is None:
            return f"Missing required argument: {key}."
    for key, val in args.items():
        if key not in props:
            return f"Unknown argument: {key}."
        if val is None:
            continue
        if not TYPE_OK[props[key]["type"]](val):
            return f"Argument {key} must be a {props[key]['type']}."
    return None


def _tool_error(message):
    return {"content": [{"type": "text", "text": message}], "isError": True}


def call_tool(name, args, shop):
    """Run a tool. Returns None for an unknown tool (a protocol error), else a CallToolResult dict."""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return None
    err = validate_arguments(tool["inputSchema"], args)
    if err:
        return _tool_error(err)
    clean = {k: v for k, v in args.items() if v is not None}
    try:
        res = HANDLERS[name](shop, **clean)
        return {"content": [{"type": "text", "text": res["say"]}], "structuredContent": res, "isError": False}
    except ledger.LedgerError as e:
        return _tool_error(str(e))
    except Exception:  # never leak internals to a voice assistant
        log.exception("tool %s failed", name)
        return _tool_error("Something went wrong on my side. Nothing was recorded.")


def _err(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(msg, shop="default"):
    """Handle one JSON-RPC message. Returns (http_status, body_or_None). None body means 202 Accepted."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return 400, _err(None, -32600, "Invalid Request: send a single JSON-RPC 2.0 message object.")
    method = msg.get("method")
    if method is None:
        if "result" in msg or "error" in msg:  # a response from the client
            return 202, None
        return 400, _err(None, -32600, "Invalid Request: missing method.")
    if not isinstance(method, str):
        return 400, _err(None, -32600, "Invalid Request: method must be a string.")
    if "id" not in msg:  # notification, e.g. notifications/initialized
        return 202, None
    rid = msg["id"]
    if isinstance(rid, bool) or not isinstance(rid, (int, str)):
        return 400, _err(None, -32600, "Invalid Request: id must be a string or integer.")
    params = msg.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return 200, _err(rid, -32602, "Invalid params: expected an object.")

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_VERSIONS else LATEST
        return 200, {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS}}
    if method == "ping":
        return 200, {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return 200, {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments")
        if not isinstance(name, str):
            return 200, _err(rid, -32602, "Invalid params: name is required.")
        result = call_tool(name, {} if args is None else args, ledger.clean_shop(shop))
        if result is None:
            return 200, _err(rid, -32602, f"Unknown tool: {name}")
        return 200, {"jsonrpc": "2.0", "id": rid, "result": result}
    return 200, _err(rid, -32601, f"Method not found: {method}")
