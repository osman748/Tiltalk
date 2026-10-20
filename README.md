# TillTalk

**A voice ledger for small shops, built as an Alexa+ MCP server.**
Say "Sold three rice at 12 each to Ama, she'll pay Friday." It is recorded. Ask "Who owes me?" and hear the answer.

Built for the **Amazon Developer Hackathon, Alexa+ track**.

- **Live demo:** https://tiltalk.onrender.com/
- **No login needed.** Open the link and tap **Load a sample day**. (The free host sleeps when idle, so the first load can take about a minute.)
- **MCP endpoint:** `https://tiltalk.onrender.com/mcp`
- **License:** MIT

## Quick start (run it yourself)

Requirements: Python 3 (tested on 3.13). No database or account needed.

```bash
git clone https://github.com/osman748/Tiltalk.git
cd Tiltalk
pip install -r requirements.txt
python3 server.py
```

Then open http://127.0.0.1:8000 in a browser.

1. Tap **Load a sample day**.
2. Type or tap an example, for example `Sold 3 rice at 12 each to Ama on credit`.
3. Ask `Who owes me?` and `How did I do today?`.
4. Open **What is happening underneath** to see the real MCP messages sent to `/mcp`.

Run the tests (38 tests, no extra packages needed):

```bash
python3 run_tests.py
```

## Try the MCP server directly

With the server running:

```bash
# 1. Handshake
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}'

# 2. List the eight tools
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'

# 3. Record a credit sale
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' -H 'X-Shop: reviewer' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"log_sale","arguments":{"item":"rice","quantity":3,"unit_price":12,"amount_paid":0,"customer":"Ama"}}}'

# 4. Ask who owes money
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' -H 'X-Shop: reviewer' \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"who_owes_me","arguments":{}}}'
```

Expected: step 3 returns `Recorded: 3 rice for GHS 36.00. Ama owes GHS 36.00.` and step 4 returns `1 person owes you GHS 36.00 in total: Ama GHS 36.00.`

The optional `X-Shop` header selects a separate ledger, so different testers do not see each other's data.

## The problem

Small traders serve customers with busy hands, so sales are logged later from memory, or never. Money owed on credit is what gets lost most. TillTalk records at the moment of the sale, by voice, and answers "who owes me?" instantly.

## How it works

```
You speak -> Alexa+ (language understanding) -> MCP tool call -> TillTalk server -> ledger rules -> spoken reply
```

**The AI understands. Plain code decides.** Alexa+ works out what you said and calls a tool. The server then checks every rule and does all arithmetic itself, in whole pesewas with exact decimals, never floats:

- no negative, zero or absurd amounts
- a credit sale needs a customer name, so the debt can be tracked
- a customer cannot pay back more than they owe
- entries of GHS 500 or more must be confirmed with a spoken "yes" first (and are re-checked when confirmed)
- undo removes only the latest entry
- internal errors never reach the voice reply

## The MCP server

A single endpoint, `/mcp`, implemented directly against **MCP spec 2025-11-25, Streamable HTTP**, with no SDK, so every rule is visible in `mcp.py` and `server.py`:

- `initialize` with version negotiation, `notifications/*` answered with `202`, `ping`
- `tools/list` with JSON Schemas and read-only / destructive hints
- `tools/call` returning both spoken text and `structuredContent`; business problems come back as tool errors (`isError`), protocol problems as JSON-RPC errors
- `Origin` validation (403 on a bad origin), `MCP-Protocol-Version` validation (400), batches rejected, optional bearer token

Eight tools: `log_sale`, `log_expense`, `record_payment`, `who_owes_me`, `daily_summary`, `list_recent`, `undo_last`, `confirm`.

## What is real and what is a stand-in

| Part | Status |
|---|---|
| MCP server, tools, ledger rules, tests | Real and working |
| Simulator page | Real. It calls `/mcp` over HTTP like a client would |
| "Alexa+" language understanding in the simulator | **A small rule-based stand-in** (`interpret.py`), so the demo works offline. On a real device Alexa+ does this |
| Deployment to a real Alexa+ device | **Not done.** Needs the Alexa AI CLI and add-on package, a public HTTPS URL, and OAuth 2.1 (PKCE) per Amazon's quickstart. Not implemented here |
| Users | Separate ledgers are keyed by an `X-Shop` header. Production would map this from the OAuth identity |

Other limits: SQLite on one server (the free host forgets data on restart), single currency per deployment, and the parser handles common English phrasings only.

## Configuration (all optional)

| Variable | Default | Meaning |
|---|---|---|
| `TILLTALK_CURRENCY` | `GHS` | Currency label |
| `TILLTALK_CONFIRM_LIMIT` | `50000` | Minor units (pesewas); entries at or above this need a yes |
| `TILLTALK_TZ_OFFSET_HOURS` | `0` | Shop's offset from UTC, for "today" |
| `TILLTALK_TOKEN` | unset | If set, `/mcp` requires `Authorization: Bearer <token>` |
| `TILLTALK_ALLOWED_ORIGINS` | unset | Comma-separated extra allowed `Origin` values |
| `TILLTALK_DB` | `./tilltalk.db` | SQLite file |
| `TILLTALK_READ_ONLY_DEMO` | unset | `1` hides the seed and reset buttons on a public demo |

## Files

| File | Role |
|---|---|
| `ledger.py` | All money rules and SQLite storage |
| `mcp.py` | MCP protocol: tools, schemas, JSON-RPC handling |
| `server.py` | HTTP endpoint, security checks, simulator helpers |
| `interpret.py` | Rule-based stand-in for Alexa+ understanding |
| `index.html` | Simulator UI |
| `test_tilltalk.py` | Ledger, parser and MCP-over-HTTP tests |
| `run_tests.py` | Test runner (no pytest needed) |
| `render.yaml`, `requirements.txt` | Deployment on Render |

## Built with AI assistance

This project was built with the help of an AI assistant (Claude).

## License

MIT, see `LICENSE`.
