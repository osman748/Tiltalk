"""Stand-in for Alexa+'s language understanding.

In production Alexa+ turns speech into MCP tool calls. For the simulator, and so the demo
works offline with no keys, this small rule-based parser plays that role. It returns either a
tool call ({"tool", "arguments"}) or a spoken reply ({"reply"}) when it needs more information.
It only extracts what was said. It never decides what is allowed: the ledger does.
"""
import re

WORDS = {"zero": 0, "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
         "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
         "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
         "hundred": 100, "dozen": 12, "half": 0.5}
NUM = r"(?:\d+(?:[.,]\d+)?|" + "|".join(sorted((w for w in WORDS if w not in ("a", "an")), key=len, reverse=True)) + r")"
UNITS = r"(?:bags?|bottles?|boxes|box|packs?|packets?|sachets?|tins?|cans?|kg|kilos?|pieces?|pcs|cups?|tubers?|loaves|loaf|bunch(?:es)?|crates?|sticks?|bars?|rolls?)"
CUR = r"(?:\s*(?:cedis?|ghs|ghc|gh₵|₵|dollars?|usd|naira|shillings?))?"
CREDIT_WORDS = r"(?:on credit|on tab|on account|will pay|'ll pay|pays|to pay|pay later|pay on|owes?)"

STOP_NAMES = {"me", "my", "the", "today", "yesterday", "it", "that", "this", "him", "her", "them", "someone", "who"}


def num(token):
    t = token.lower().replace(",", ".") if re.fullmatch(r"\d+,\d+", token) else token.lower().replace(",", "")
    if t in WORDS:
        return WORDS[t]
    try:
        return float(t)
    except ValueError:
        return None


def clean(text):
    t = text.strip()
    t = re.sub(r"^(?:hey |hi |ok(?:ay)? )?(?:alexa|tilltalk)[,:]?\s*", "", t, flags=re.I)
    t = re.sub(r"^(?:ask|tell) tilltalk (?:to |that )?", "", t, flags=re.I)
    t = re.sub(r"^please\s+", "", t, flags=re.I)
    return t.strip().rstrip(".!?").strip()


def _name(s):
    m = re.search(r"\b(?:to|from|for)\s+([A-Za-z][A-Za-z'-]*(?:\s+[A-Z][A-Za-z'-]*)?)", s)
    if m and m.group(1).lower() not in STOP_NAMES:
        return m.group(1)
    return None


def interpret(text, has_pending=False):
    raw = text or ""
    t = clean(raw)
    low = t.lower()
    if not t:
        return {"reply": "I didn't catch that. Try: sold three rice at 12 each."}

    # ---- confirmation of a large entry
    if has_pending and re.fullmatch(r"(?:yes|yeah|yep|yup|confirm(?:ed)?|do it|go ahead|correct|that's right|ok(?:ay)?)", low):
        return {"tool": "confirm", "arguments": {"approve": True}}
    if has_pending and re.fullmatch(r"(?:no|nope|cancel|stop|don't|do not|never mind|nevermind)", low):
        return {"tool": "confirm", "arguments": {"approve": False}}

    # ---- undo
    if re.search(r"\b(?:undo|take that back|delete (?:the )?last|remove (?:the )?last|scratch that|that was wrong)\b", low):
        return {"tool": "undo_last", "arguments": {}}

    # ---- questions about debts
    if re.search(r"\bwho (?:still )?owes\b|\bwho hasn'?t paid\b|\bunpaid\b|\boutstanding\b|\bdebtors?\b|\bowed to me\b", low):
        return {"tool": "who_owes_me", "arguments": {}}
    m = re.search(r"\bdoes\s+([A-Za-z][A-Za-z'-]*)\s+owe\b|\bhow much does\s+([A-Za-z][A-Za-z'-]*)\s+owe\b|"
                  r"\bhow much (?:is )?([A-Za-z][A-Za-z'-]*)\s+owing\b", t, flags=re.I)
    if m:
        who = next(g for g in m.groups() if g)
        if who.lower() not in STOP_NAMES:
            return {"tool": "who_owes_me", "arguments": {"customer": who}}
    if re.search(r"\bhow much (?:do|does) .{0,20}owe me\b", low):
        return {"tool": "who_owes_me", "arguments": {}}

    # ---- summaries
    if re.search(r"\bhow (?:did|am|have) i (?:do|doing|done)\b|\bsummary\b|\bhow was (?:today|business)\b|"
                 r"\bhow much (?:did i|have i) (?:make|made|earn|earned|sell|sold)\b|\bmy (?:sales|profit|takings)\b|"
                 r"\bend of day\b|\bclose (?:the )?(?:day|shop|books)\b", low):
        args = {}
        if "yesterday" in low:
            args["date"] = "__yesterday__"
        return {"tool": "daily_summary", "arguments": args}

    if re.search(r"\bwhat (?:did i|have i|was) (?:record|log|enter|just)|\bread (?:that |it )?back\b|\brecent\b|"
                 r"\blast (?:few )?(?:entries|sales)\b|\bwhat have i (?:sold|recorded)\b", low):
        return {"tool": "list_recent", "arguments": {}}

    # ---- a customer paying back
    m = re.match(rf"^(?P<name>[A-Za-z][A-Za-z'-]*(?:\s+[A-Z][A-Za-z'-]*)?)\s+(?:has\s+|just\s+)?(?:paid|brought|gave|sent|settled)"
                 rf"(?:\s+me)?(?:\s+back)?\s+(?P<amt>{NUM}){CUR}\b(?!\s+(?:for|of)\b)", t, flags=re.I)
    if not m:
        m2 = re.match(rf"^(?:i\s+)?(?:received|got|collected)\s+(?P<amt>{NUM}){CUR}\s+from\s+(?P<name>[A-Za-z][A-Za-z'-]*(?:\s+[A-Z][A-Za-z'-]*)?)", t, flags=re.I)
        m = m2
    if m and m.group("name").lower() not in STOP_NAMES and not low.startswith(("sold", "i sold")):
        return {"tool": "record_payment", "arguments": {"customer": m.group("name"), "amount": num(m.group("amt"))}}

    # ---- an expense
    m = re.match(rf"^(?:i\s+)?(?:spent|paid|bought for|expense)\s+(?P<amt>{NUM}){CUR}\s*(?:on|for)\s+(?P<what>.+)$", t, flags=re.I)
    if m:
        return {"tool": "log_expense", "arguments": {"description": m.group("what").strip(), "amount": num(m.group("amt"))}}

    # ---- a sale
    m = re.match(rf"^(?:i\s+)?(?:just\s+)?(?:sold|sell|selling)\s+(?:(?P<qty>{NUM}|an?)\s+)?(?:{UNITS}\s+of\s+)?(?P<rest>.+)$", t, flags=re.I)
    if m:
        return _sale(m, t)

    return {"reply": "Sorry, I didn't understand. You can say things like: sold three rice at 12 each to Ama on credit, "
                     "spent 15 on transport, Kofi paid 5, who owes me, or how did I do today."}


def _sale(m, full):
    qty_tok = m.group("qty")
    rest = m.group("rest").strip()
    qty = 1 if (qty_tok is None or qty_tok.lower() in ("a", "an")) else num(qty_tok)
    if qty is None:
        return {"reply": "How many did you sell?"}

    # the item runs until a price / customer / credit word
    cut = re.search(rf"\s+(?:at|for|@)\s+|\s+to\s+[A-Za-z]|,|\s+on\s+(?:credit|tab|account)\b|\s+and\s+|\s+{CREDIT_WORDS}", rest, flags=re.I)
    item = (rest[:cut.start()] if cut else rest).strip()
    item = re.sub(rf"^(?:{UNITS}\s+of\s+)", "", item, flags=re.I).strip()
    if not item or item.lower() in STOP_NAMES:
        return {"reply": "What did you sell?"}
    tail = rest[cut.start():] if cut else ""

    args = {"item": item, "quantity": qty}

    pm = re.search(rf"(?:\bat\b|\bfor\b|@)\s*(?P<p>{NUM}){CUR}(?P<each>\s*(?:each|apiece|a piece|per|a bag|a bottle|a box|a pack|a kilo|every))?", tail, flags=re.I)
    if not pm:
        return {"reply": f"What price did you sell the {item} at?"}
    price = num(pm.group("p"))
    if price is None:
        return {"reply": "I didn't catch the price."}
    is_each = bool(pm.group("each")) or re.match(r"\s*at\b|\s*@", pm.group(0), flags=re.I) is not None
    if is_each:
        args["unit_price"] = price
    else:
        args["total_price"] = price

    cust = _name(tail)
    if cust:
        args["customer"] = cust
    paid = re.search(rf"\b(?:paid|pays|gave|put down|deposit(?:ed)?)\s+(?P<a>{NUM}){CUR}", tail, flags=re.I)
    if paid:
        args["amount_paid"] = num(paid.group("a"))
    elif re.search(CREDIT_WORDS, tail, flags=re.I) or re.search(r"\bon\s+(?:credit|tab|account)\b", tail, flags=re.I):
        args["amount_paid"] = 0
    note = re.search(r"(?:she|he|they)(?:'ll| will)\s+pay\s+(?:on\s+)?([A-Za-z]+)|pays?\s+(?:on\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|next week)", tail, flags=re.I)
    if note:
        args["note"] = "pays " + next(g for g in note.groups() if g).lower()
    return {"tool": "log_sale", "arguments": args}
