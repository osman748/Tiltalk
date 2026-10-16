# TillTalk: Devpost submission pack

Deadline: **Fri Oct 23, 2026, 7:00 PM GMT.** Track: **Alexa+**. Optional mini challenge: **Open Source** (needs an extra *new* open-source project or a PR/fork, read the rules before choosing).

## 1. Project description (paste into Devpost)

**Tagline:** A voice ledger for small shops. Say what you sold, ask who owes you.

**Inspiration.** Small traders serve customers with busy hands, so sales get written down later from memory, or never. The money that slips away most is credit: "she'll pay Friday". I wanted a shop owner to log a sale in the moment, by voice, and to be able to ask "who owes me?" and get a straight answer.

**What it does.** TillTalk is an Alexa+ MCP server for a shop's books. You say "Sold three rice at 12 each to Ama, she'll pay Friday" and it records the sale and the debt. You can log expenses, record a customer paying back, ask who owes you, hear how your day went, read back recent entries, and undo a mistake. Big entries wait for a spoken "yes" before they are recorded.

**How it works.** Alexa+ understands the speech and calls tools on my self-hosted MCP server (Streamable HTTP, spec 2025-11-25). The server has eight tools. The key design choice: the AI understands, plain code decides. Every rule lives in the server: exact decimal money in whole pesewas, no negatives or absurd amounts, a credit sale must name a customer, nobody can pay back more than they owe, and entries over GHS 500 need confirmation (re-checked when confirmed). A misheard number or a clever sentence cannot bypass them, and internal errors are never spoken.

**How I built it.** Python and Flask, with the MCP protocol written by hand (no SDK) so every rule is visible. SQLite for storage. A phone-friendly simulator page that sends real JSON-RPC to the `/mcp` endpoint and shows the traffic live. 38 automated tests cover the ledger rules, the parser, and the MCP protocol over real HTTP, including origin validation, version headers, auth, and error handling.

**Challenges.** Getting money right (no floating-point drift), making voice replies short and natural, and deciding what the server should refuse versus ask about. For example, a credit sale without a customer name is refused with a clear question, because an untracked debt is worthless.

**What's next.** Deploy to a real Alexa+ device with OAuth 2.1 (PKCE) and the add-on package, support more currencies and languages, mobile money and receipts, and weekly summaries.

**Honest note.** The demo's language understanding is a small rule-based stand-in for Alexa+ so it runs offline. The MCP server, ledger rules and tests are real. I have not deployed it to a physical Alexa+ device or implemented the OAuth flow yet.

## 2. Demo video script (under 3 minutes, English, public on YouTube or Vimeo)

Lead with the best material. Judges may stop at 3:00. Screen-record the simulator on your phone.

| Time | Show | Say |
|---|---|---|
| 0:00 to 0:20 | The page, phone view | "Small traders log sales from memory, and the money they lose most is credit. TillTalk lets you log a sale by voice and ask who owes you." |
| 0:20 to 0:50 | Tap Mic, say: "Sold three rice at twelve each to Ama on credit, she'll pay Friday" | "I just speak. The reply tells me Ama owes thirty-six cedis." |
| 0:50 to 1:15 | Say: "Who owes me?" | "Instant answer, no notebook." |
| 1:15 to 1:50 | Scroll to the MCP traffic panel | "Every message becomes a real MCP call to my server: initialize, tools/list, then tools/call. This is the same Streamable HTTP interface Alexa+ uses." |
| 1:50 to 2:20 | Type "Kofi paid 500" (error), then "Sold 100 bags of cement at 90 each", then "yes" | "The AI only understands. The server decides. It refuses a payment bigger than the debt, and holds a big sale until I confirm." |
| 2:20 to 2:45 | "How did I do today?" | "End of day in one sentence: sales, spending, profit, and what customers still owe." |
| 2:45 to 3:00 | Repo page with the MIT license | "Open source, 38 tests, built with Python. Thank you." |

## 3. Product feedback (required; write YOUR honest experience)

Amazon asks for feedback on every tool, API or SDK you used: what you used it for, what worked, what needs work, how onboarding felt, and whether you would build with it again. **Write this yourself from what you actually did.** Use this outline:

- **MCP (spec 2025-11-25, Streamable HTTP):** used to build the server. What was clear? What was confusing?
- **Alexa+ MCP toolkit docs:** the quickstart, auth requirements, the 500 ms latency target. Was anything hard to find or unclear?
- **Any other tool you touched** (Alexa AI CLI, simulator, Local Inspector): only list ones you really used.

**Things I noticed while reading the docs (check they match your own experience before using):**
- The quickstart requires OAuth 2.1 with PKCE before a first deploy, which is a big step for a small demo.
- It says Dynamic Client Registration is not yet supported.
- The quickstart page did not state tool-name or tool-description requirements; it points to a separate design guide.

**Optional friction log (up to 10% judging bonus).** For each real problem: the task, the steps, what you expected vs what happened, severity, workaround, suggested fix. Only log things that really happened to you.

## 4. Submission checklist

- [ ] Joined the hackathon (done) and started a project on Devpost
- [ ] Track selected: **Alexa+**
- [ ] Public GitHub repo with all source, including the `LICENSE` file (MIT)
- [ ] Repo calls MCP in code (it does: `mcp.py`, `server.py`)
- [ ] Demo video under 3 minutes, public, in English, no copyrighted music
- [ ] Description pasted from section 1
- [ ] Product feedback written from your own experience
- [ ] A public demo URL if you can deploy (optional; see `render.yaml`). Set `TILLTALK_READ_ONLY_DEMO=1` on any public demo
- [ ] Submitted well before **7:00 PM GMT on Oct 23**
