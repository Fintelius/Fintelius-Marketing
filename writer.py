"""Email writer. The model writes only the middle; greeting and signature are fixed in code."""
import html
import db
import llm
import guard
import profile as P

SYS = (
    "You write short, personal, professional business emails for the Managing Director of an Indian equipment "
    "manufacturer. Rules: plain simple English; 90 to 140 words; no prices, no quotations, no discounts, no delivery "
    "promises, no guarantees; no flattery; no marketing buzzwords; mention one concrete reason this company may need "
    "our products (use the project or company facts given, do not invent facts); one clear ask: that we be considered "
    "for their vendor list / enquiries, and how to register if they have a process. Do not write a greeting or "
    "signature. Reply with JSON only: {\"subject\": \"...\", \"body\": \"...\"}")


def _context(con, contact):
    comp = con.execute("SELECT * FROM companies WHERE id=?", (contact["company_id"],)).fetchone()
    proj = None
    if comp:
        proj = con.execute("SELECT * FROM projects WHERE lower(client) LIKE ? ORDER BY id DESC LIMIT 1",
                           ("%" + comp["name"].lower()[:20] + "%",)).fetchone()
    return comp, proj


def _assemble(body):
    text = "Dear Sir,\n\n" + body.strip() + "\n\n" + P.UNSUB_LINE + "\n\n" + P.signature_text()
    h = ("<p>Dear Sir,</p>" + "".join(f"<p>{html.escape(par)}</p>" for par in body.strip().split("\n\n")) +
         f"<p>{html.escape(P.UNSUB_LINE)}</p>" + P.signature_html())
    return text, h


def draft_intro(con, contact):
    comp, proj = _context(con, contact)
    facts = [f"About us: {P.FACTS}"]
    if comp:
        facts.append(f"Recipient company: {comp['name']} ({comp['type'] or 'type unknown'}), {comp['country'] or ''}, "
                     f"industry: {comp['industry'] or ''}. Note: {comp['notes'] or ''}")
    if proj:
        facts.append(f"Relevant project: {proj['name']} for {proj['client']}, {proj['location']}, stage {proj['stage']}. "
                     f"{proj['summary']}")
    facts.append(f"Recipient role: {contact['designation'] or 'unknown'} {contact['department'] or ''}")
    facts.append("Products to mention (choose the ones that fit): " + ", ".join(P.PRODUCTS))
    out = llm.ask_json(SYS, "\n".join(facts), max_tokens=900)
    if not isinstance(out, dict) or not out.get("subject") or not out.get("body"):
        return None
    text, h = _assemble(out["body"])
    problems = guard.check_draft(out["subject"], text)
    cur = con.execute(
        "INSERT INTO emails(contact_id,kind,subject,body,status,problems,created_at) VALUES(?,?,?,?,?,?,?)",
        (contact["id"], "intro", out["subject"].strip(), text, "blocked" if problems else "draft",
         "; ".join(problems), db.now()))
    con.commit()
    return cur.lastrowid


def html_for(email_row):
    """Rebuild HTML from stored plain text so approval edits stay in sync."""
    body = email_row["body"]
    paras = [p for p in body.split("\n\n")]
    # last paragraph is the signature block (contains newlines); render it with the fixed HTML signature
    sig = P.signature_text()
    core = body[: body.rfind(sig)].strip() if sig in body else body
    parts = [p for p in core.split("\n\n") if p.strip()]
    out = "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in parts)
    return out + P.signature_html()


def draft_followups(con, limit=5):
    """One polite follow-up, 7+ days after an intro with no reply, never more than one."""
    rows = con.execute(
        "SELECT c.* FROM contacts c JOIN emails e ON e.contact_id=c.id AND e.kind='intro' AND e.status='sent' "
        "WHERE c.status='emailed' AND date(e.sent_at) <= date('now','-7 day') "
        "AND NOT EXISTS (SELECT 1 FROM emails f WHERE f.contact_id=c.id AND f.kind='followup') LIMIT ?",
        (limit,)).fetchall()
    n = 0
    for c in rows:
        sys = SYS.replace("a short, personal", "a very short (50 to 70 words), polite follow-up")
        out = llm.ask_json(sys, f"This is a follow-up to our introduction email sent a week ago to {c['designation'] or 'a procurement contact'}. "
                                f"About us: {P.FACTS}", max_tokens=600)
        if not isinstance(out, dict) or not out.get("body"):
            continue
        text, _ = _assemble(out["body"])
        problems = guard.check_draft(out.get("subject", "Following up"), text) if len(text) >= 200 else ["too short"]
        # follow-ups are shorter; only price/commitment problems are blocking
        problems = [p for p in problems if "length" not in p and "too short" not in p]
        con.execute("INSERT INTO emails(contact_id,kind,subject,body,status,problems,created_at) VALUES(?,?,?,?,?,?,?)",
                    (c["id"], "followup", out.get("subject") or "Following up", text,
                     "blocked" if problems else "draft", "; ".join(problems), db.now()))
        n += 1
    con.commit()
    return n
