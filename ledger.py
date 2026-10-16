"""TillTalk ledger. Every rule about money lives here, in plain code.

The assistant (Alexa+, or the simulator) only works out *what was said* and calls a tool.
This module decides what is allowed, does the arithmetic in whole minor units (pesewas),
and keeps the books consistent. A misheard number or a clever prompt cannot bypass it.
"""
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

DB_PATH = os.environ.get("TILLTALK_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tilltalk.db"))
CURRENCY = os.environ.get("TILLTALK_CURRENCY", "GHS")
# Entries at or above this amount (in minor units, so 50000 = 500.00) need a spoken "yes".
CONFIRM_LIMIT_MINOR = int(os.environ.get("TILLTALK_CONFIRM_LIMIT", "50000"))
TZ_OFFSET_HOURS = float(os.environ.get("TILLTALK_TZ_OFFSET_HOURS", "0"))
MAX_ENTRIES = int(os.environ.get("TILLTALK_MAX_ENTRIES", "5000"))
MAX_MAJOR = Decimal("10000000")
PENDING_TTL_SECONDS = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shop TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('sale','expense','payment')),
  ts TEXT NOT NULL,
  day TEXT NOT NULL,
  item TEXT,
  qty TEXT,
  total_minor INTEGER NOT NULL CHECK(total_minor >= 0),
  paid_minor INTEGER NOT NULL CHECK(paid_minor >= 0),
  customer TEXT,
  customer_key TEXT,
  note TEXT,
  voided INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_entries_shop_day ON entries(shop, day);
CREATE INDEX IF NOT EXISTS idx_entries_shop_cust ON entries(shop, customer_key);
CREATE TABLE IF NOT EXISTS pending(
  token TEXT NOT NULL,
  shop TEXT NOT NULL,
  payload TEXT NOT NULL,
  summary TEXT NOT NULL,
  created REAL NOT NULL,
  expires REAL NOT NULL,
  PRIMARY KEY (shop, token)
);
"""


class LedgerError(Exception):
    """A business-rule problem. The message is safe and useful to read out loud."""


_schema_lock = threading.Lock()
_schema_ready = set()


@contextmanager
def _tx(write=True):
    with _schema_lock:
        if DB_PATH not in _schema_ready:
            boot = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
            boot.executescript(SCHEMA)
            boot.close()
            _schema_ready.add(DB_PATH)
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------- helpers

def clean_shop(value):
    s = str(value or "")
    return s if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", s) else "default"


def now_local():
    return datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET_HOURS)


def today():
    return now_local().strftime("%Y-%m-%d")


def yesterday():
    return (now_local() - timedelta(days=1)).strftime("%Y-%m-%d")


def fmt(minor):
    sign = "-" if minor < 0 else ""
    m = abs(int(minor))
    return f"{sign}{CURRENCY} {m // 100:,}.{m % 100:02d}"


def _dec(value, field):
    if value is None or isinstance(value, bool):
        raise LedgerError(f"{field} must be a number.")
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation:
        raise LedgerError(f"{field} must be a number.")
    if not d.is_finite():
        raise LedgerError(f"{field} must be a real number.")
    return d


def to_minor(value, field, allow_zero=False):
    d = _dec(value, field)
    if d < 0 or (d == 0 and not allow_zero):
        raise LedgerError(f"{field} must be more than zero.")
    if d > MAX_MAJOR:
        raise LedgerError(f"{field} is too large to be right. Please check it.")
    return int((d * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _text(value, field, max_len, required=True):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise LedgerError(f"{field} is required.")
        return None
    if not isinstance(value, str):
        raise LedgerError(f"{field} must be text.")
    s = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    s = " ".join(s.split())
    if len(s) > max_len:
        raise LedgerError(f"{field} is too long.")
    return s


def _name(value, required):
    s = _text(value, "customer", 60, required)
    return (s[:1].upper() + s[1:]) if s else None


def _qty_str(q):
    return format(q.normalize(), "f")


def _balance(conn, shop, key):
    r = conn.execute(
        """SELECT COALESCE(SUM(CASE kind WHEN 'sale' THEN total_minor - paid_minor
                                         WHEN 'payment' THEN -paid_minor ELSE 0 END), 0) AS b
           FROM entries WHERE shop=? AND customer_key=? AND voided=0""",
        (shop, key)).fetchone()
    return int(r["b"])


def _latest_name(conn, shop, key):
    r = conn.execute("SELECT customer FROM entries WHERE shop=? AND customer_key=? ORDER BY id DESC LIMIT 1",
                     (shop, key)).fetchone()
    return r["customer"] if r else key


def _check_capacity(conn, shop):
    n = conn.execute("SELECT COUNT(*) c FROM entries WHERE shop=?", (shop,)).fetchone()["c"]
    if n >= MAX_ENTRIES:
        raise LedgerError("This ledger is full. Please start a fresh one.")


def _describe(p):
    if p["kind"] == "sale":
        who = f" to {p['customer']}" if p.get("customer") else ""
        return f"sale of {p['qty']} {p['item']} for {fmt(p['total_minor'])}{who}"
    if p["kind"] == "expense":
        return f"expense: {p['item']}, {fmt(p['total_minor'])}"
    return f"payment of {fmt(p['total_minor'])} from {p['customer']}"


# ---------------------------------------------------------------- prepare (validate only)

def _prepare_sale(item, quantity, unit_price, total_price, amount_paid, customer, note):
    item = _text(item, "item", 80)
    qty = _dec(quantity, "quantity")
    if qty <= 0 or qty > 100000:
        raise LedgerError("quantity must be more than zero and not unreasonably large.")
    qty = qty.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    if qty <= 0:
        raise LedgerError("quantity is too small.")
    if (unit_price is None) == (total_price is None):
        raise LedgerError("Give either the price each or the total price, not both and not neither.")
    if unit_price is not None:
        unit_minor = to_minor(unit_price, "unit_price")
        total_minor = int((Decimal(unit_minor) * qty).to_integral_value(rounding=ROUND_HALF_UP))
    else:
        total_minor = to_minor(total_price, "total_price")
    if total_minor <= 0:
        raise LedgerError("That sale adds up to nothing. Please check the numbers.")
    if total_minor > int(MAX_MAJOR * 100):
        raise LedgerError("That sale total is too large to be right. Please check it.")
    cust = _name(customer, False)
    paid = total_minor if amount_paid is None else to_minor(amount_paid, "amount_paid", allow_zero=True)
    if paid > total_minor:
        raise LedgerError(f"The amount paid ({fmt(paid)}) is more than the sale total ({fmt(total_minor)}).")
    if total_minor - paid > 0 and not cust:
        raise LedgerError("A credit or part-paid sale needs a customer name so the debt can be tracked.")
    return {"kind": "sale", "item": item, "qty": _qty_str(qty), "total_minor": total_minor, "paid_minor": paid,
            "customer": cust, "note": _text(note, "note", 120, False)}


def _prepare_expense(description, amount, category):
    total = to_minor(amount, "amount")
    return {"kind": "expense", "item": _text(description, "description", 80), "qty": None, "total_minor": total,
            "paid_minor": total, "customer": None, "note": _text(category, "category", 40, False)}


def _prepare_payment(customer, amount):
    total = to_minor(amount, "amount")
    return {"kind": "payment", "item": None, "qty": None, "total_minor": total, "paid_minor": total,
            "customer": _name(customer, True), "note": None}


def _validate_payment(conn, shop, p):
    key = p["customer"].lower()
    bal = _balance(conn, shop, key)
    name = _latest_name(conn, shop, key)
    if bal <= 0:
        raise LedgerError(f"{p['customer']} doesn't owe anything right now.")
    if p["total_minor"] > bal:
        raise LedgerError(f"{name} only owes {fmt(bal)}, so {fmt(p['total_minor'])} is too much. Please check the amount.")


# ---------------------------------------------------------------- commit

def _insert(conn, shop, p):
    if p["kind"] == "payment":
        _validate_payment(conn, shop, p)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    key = p["customer"].lower() if p.get("customer") else None
    cur = conn.execute(
        """INSERT INTO entries(shop,kind,ts,day,item,qty,total_minor,paid_minor,customer,customer_key,note)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (shop, p["kind"], ts, today(), p.get("item"), p.get("qty"), p["total_minor"], p["paid_minor"],
         p.get("customer"), key, p.get("note")))
    res = {"status": "recorded", "id": cur.lastrowid, "kind": p["kind"], "total": fmt(p["total_minor"]),
           "total_minor": p["total_minor"], "paid_minor": p["paid_minor"]}
    if p["kind"] == "sale":
        owed = p["total_minor"] - p["paid_minor"]
        say = f"Recorded: {p['qty']} {p['item']} for {fmt(p['total_minor'])}"
        if owed == 0:
            say += ", paid in full."
        else:
            if p["paid_minor"] > 0:
                say += f". {p['customer']} paid {fmt(p['paid_minor'])} and owes {fmt(owed)}."
            else:
                say += f". {p['customer']} owes {fmt(owed)}."
            bal = _balance(conn, shop, key)
            if bal != owed:
                say += f" {p['customer']} now owes {fmt(bal)} in total."
            res["customer_balance_minor"] = bal
        res["owed_minor"] = owed
    elif p["kind"] == "expense":
        say = f"Recorded expense: {p['item']}, {fmt(p['total_minor'])}."
    else:
        bal = _balance(conn, shop, key)
        res["customer_balance_minor"] = bal
        say = f"Recorded: {p['customer']} paid {fmt(p['total_minor'])}. "
        say += f"{p['customer']} still owes {fmt(bal)}." if bal > 0 else f"{p['customer']} is all paid up."
    res["say"] = say
    return res


def _submit(shop, p):
    shop = clean_shop(shop)
    with _tx() as conn:
        _check_capacity(conn, shop)
        if p["kind"] == "payment":
            _validate_payment(conn, shop, p)
        if p["total_minor"] >= CONFIRM_LIMIT_MINOR:
            conn.execute("DELETE FROM pending WHERE shop=? AND expires<?", (shop, time.time()))
            if conn.execute("SELECT COUNT(*) c FROM pending WHERE shop=?", (shop,)).fetchone()["c"] >= 5:
                raise LedgerError("Too many things are waiting for confirmation. Please confirm or cancel them first.")
            token = secrets.token_hex(3)
            summary = _describe(p)
            now = time.time()
            conn.execute("INSERT INTO pending(token,shop,payload,summary,created,expires) VALUES(?,?,?,?,?,?)",
                         (token, shop, json.dumps(p), summary, now, now + PENDING_TTL_SECONDS))
            return {"status": "needs_confirmation", "token": token, "summary": summary,
                    "expires_in_seconds": PENDING_TTL_SECONDS,
                    "say": f"That's a big one, {fmt(p['total_minor'])}. Please confirm: {summary}. "
                           "Say yes to record it or no to cancel."}
        return _insert(conn, shop, p)


# ---------------------------------------------------------------- public tool functions

def log_sale(shop, item, quantity, unit_price=None, total_price=None, amount_paid=None, customer=None, note=None):
    return _submit(shop, _prepare_sale(item, quantity, unit_price, total_price, amount_paid, customer, note))


def log_expense(shop, description, amount, category=None):
    return _submit(shop, _prepare_expense(description, amount, category))


def record_payment(shop, customer, amount):
    return _submit(shop, _prepare_payment(customer, amount))


def confirm(shop, token=None, approve=True):
    shop = clean_shop(shop)
    with _tx() as conn:
        conn.execute("DELETE FROM pending WHERE shop=? AND expires<?", (shop, time.time()))
        if token:
            row = conn.execute("SELECT * FROM pending WHERE shop=? AND token=?", (shop, str(token))).fetchone()
        else:
            row = conn.execute("SELECT * FROM pending WHERE shop=? ORDER BY created DESC LIMIT 1", (shop,)).fetchone()
        if not row:
            raise LedgerError("Nothing is waiting for confirmation.")
        conn.execute("DELETE FROM pending WHERE shop=? AND token=?", (shop, row["token"]))
        if approve is False:
            return {"status": "cancelled", "say": "Okay, cancelled. Nothing was recorded."}
        return _insert(conn, shop, json.loads(row["payload"]))


def undo_last(shop):
    shop = clean_shop(shop)
    with _tx() as conn:
        r = conn.execute("SELECT * FROM entries WHERE shop=? AND voided=0 ORDER BY id DESC LIMIT 1", (shop,)).fetchone()
        if not r:
            raise LedgerError("There is nothing to undo.")
        conn.execute("UPDATE entries SET voided=1 WHERE id=?", (r["id"],))
        if r["customer_key"] and _balance(conn, shop, r["customer_key"]) < 0:
            raise LedgerError(f"I can't undo that yet, because {r['customer']} has paid since. Undo that payment first.")
        p = {"kind": r["kind"], "item": r["item"], "qty": r["qty"], "total_minor": r["total_minor"],
             "customer": r["customer"]}
        return {"status": "undone", "id": r["id"], "say": f"Done. I removed the {_describe(p)}."}


def who_owes_me(shop, customer=None):
    shop = clean_shop(shop)
    with _tx(write=False) as conn:
        if customer:
            name = _name(customer, True)
            bal = _balance(conn, shop, name.lower())
            shown = _latest_name(conn, shop, name.lower()) if bal else name
            say = f"{shown} owes you {fmt(bal)}." if bal > 0 else f"{name} doesn't owe you anything."
            return {"customer": shown, "owed_minor": max(bal, 0), "say": say}
        rows = conn.execute(
            """SELECT customer_key,
                      SUM(CASE kind WHEN 'sale' THEN total_minor - paid_minor WHEN 'payment' THEN -paid_minor ELSE 0 END) AS bal
               FROM entries WHERE shop=? AND voided=0 AND customer_key IS NOT NULL
               GROUP BY customer_key HAVING bal > 0 ORDER BY bal DESC""", (shop,)).fetchall()
        debtors = [{"customer": _latest_name(conn, shop, r["customer_key"]), "owed_minor": int(r["bal"]),
                    "owed": fmt(r["bal"])} for r in rows]
    total = sum(d["owed_minor"] for d in debtors)
    if not debtors:
        return {"debtors": [], "total_owed_minor": 0, "say": "Nobody owes you anything. Your books are clear."}
    top = ", ".join(f"{d['customer']} {d['owed']}" for d in debtors[:5])
    more = f", and {len(debtors) - 5} more" if len(debtors) > 5 else ""
    return {"debtors": debtors, "total_owed_minor": total, "total_owed": fmt(total),
            "say": f"{len(debtors)} {'person owes' if len(debtors) == 1 else 'people owe'} you {fmt(total)} in total: {top}{more}."}


def daily_summary(shop, date=None):
    shop = clean_shop(shop)
    day = today()
    if date:
        try:
            day = datetime.strptime(str(date), "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            raise LedgerError("The date must look like 2026-10-16.")
    with _tx(write=False) as conn:
        agg = {r["kind"]: r for r in conn.execute(
            """SELECT kind, COALESCE(SUM(total_minor),0) t, COALESCE(SUM(paid_minor),0) p, COUNT(*) n
               FROM entries WHERE shop=? AND day=? AND voided=0 GROUP BY kind""", (shop, day)).fetchall()}
        top = conn.execute(
            """SELECT lower(item) item, SUM(total_minor) t FROM entries
               WHERE shop=? AND day=? AND voided=0 AND kind='sale' GROUP BY lower(item) ORDER BY t DESC LIMIT 3""",
            (shop, day)).fetchall()
        owed_all = conn.execute(
            """SELECT COALESCE(SUM(bal),0) s FROM (
                 SELECT SUM(CASE kind WHEN 'sale' THEN total_minor - paid_minor WHEN 'payment' THEN -paid_minor ELSE 0 END) bal
                 FROM entries WHERE shop=? AND voided=0 AND customer_key IS NOT NULL
                 GROUP BY customer_key HAVING bal > 0)""", (shop,)).fetchone()["s"]

    def g(kind, col):
        return int(agg[kind][col]) if kind in agg else 0

    sales, sale_paid, n_sales = g("sale", "t"), g("sale", "p"), g("sale", "n")
    expenses, pay_in = g("expense", "t"), g("payment", "p")
    credit = sales - sale_paid
    cash_in = sale_paid + pay_in
    profit = sales - expenses
    res = {"date": day, "sales_minor": sales, "expenses_minor": expenses, "profit_minor": profit,
           "cash_in_minor": cash_in, "credit_given_minor": credit, "payments_received_minor": pay_in,
           "outstanding_minor": int(owed_all), "sales_count": n_sales,
           "top_items": [{"item": r["item"], "sales_minor": int(r["t"])} for r in top]}
    label = "Today" if day == today() else f"On {day}"
    if not agg:
        res["say"] = f"Nothing is recorded for {'today' if day == today() else day} yet."
        return res
    up = "up" if profit >= 0 else "down"
    say = (f"{label} you sold {fmt(sales)} across {n_sales} {'sale' if n_sales == 1 else 'sales'} "
           f"and spent {fmt(expenses)}, so you're {up} {fmt(abs(profit))}.")
    if credit > 0:
        say += f" {fmt(credit)} of today's sales is on credit."
    if pay_in > 0:
        say += f" You collected {fmt(pay_in)} in old debts."
    if top:
        say += f" Best seller: {top[0]['item']}."
    if owed_all > 0:
        say += f" In total, customers owe you {fmt(owed_all)}."
    res["say"] = say
    return res


def list_recent(shop, limit=5):
    shop = clean_shop(shop)
    lim = int(_dec(limit, "limit"))
    if not 1 <= lim <= 20:
        raise LedgerError("limit must be between 1 and 20.")
    with _tx(write=False) as conn:
        rows = conn.execute("SELECT * FROM entries WHERE shop=? AND voided=0 ORDER BY id DESC LIMIT ?",
                            (shop, lim)).fetchall()
    entries = [{"id": r["id"], "kind": r["kind"], "item": r["item"], "qty": r["qty"], "customer": r["customer"],
                "total": fmt(r["total_minor"]), "paid": fmt(r["paid_minor"]), "day": r["day"], "note": r["note"]}
               for r in rows]
    if not entries:
        return {"entries": [], "say": "You haven't recorded anything yet."}
    parts = []
    for e in entries[:5]:
        if e["kind"] == "sale":
            parts.append(f"sold {e['qty']} {e['item']} for {e['total']}")
        elif e["kind"] == "expense":
            parts.append(f"spent {e['total']} on {e['item']}")
        else:
            parts.append(f"{e['customer']} paid {e['total']}")
    return {"entries": entries, "say": "Latest first: " + "; ".join(parts) + "."}


# ---------------------------------------------------------------- demo helpers

def clear_shop(shop):
    shop = clean_shop(shop)
    with _tx() as conn:
        conn.execute("DELETE FROM entries WHERE shop=?", (shop,))
        conn.execute("DELETE FROM pending WHERE shop=?", (shop,))


def seed_demo(shop):
    """Add a believable day of trading through the normal rules, so the demo has something to show."""
    shop = clean_shop(shop)
    with _tx(write=False) as conn:
        if conn.execute("SELECT COUNT(*) c FROM entries WHERE shop=?", (shop,)).fetchone()["c"]:
            raise LedgerError("This ledger already has entries. Clear it first to load the sample day.")
    log_sale(shop, "rice", 3, unit_price=12, amount_paid=0, customer="Ama", note="pays friday")
    log_sale(shop, "sugar", 2, unit_price=8)
    log_sale(shop, "bread", 5, unit_price=5, amount_paid=10, customer="Kofi")
    log_expense(shop, "transport", 15)
    log_expense(shop, "electricity", 30, category="bills")
    log_sale(shop, "eggs", 1, total_price=45)
    record_payment(shop, "Kofi", 5)
    return {"status": "seeded", "say": "I loaded a sample day of trading."}
