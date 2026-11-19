"""Email writer. The model writes only the middle; greeting and signature are fixed in code."""
import html
import db
import llm
import guard
from config import settings
import profile as P

SYS = (
    "You write short, plain, professional business emails for the Managing Director of an Indian equipment "
    "manufacturer, to a company that may buy from him. Rules: simple English; 70 to 110 words in total; three short "
    "paragraphs. Paragraph 1: who we are in one sentence (manufacturer in Ahmedabad since 2012; ADNOC AVL no. as given) "
    "and the products that fit this recipient. Paragraph 2: one sentence on why we are writing; state ONLY what the "
    "given facts say about the recipient, softly (for example 'we understand you work in refinery projects'); never "
    "claim to know their projects, contracts or needs unless a project is given in the facts; never invent anything. "
    "Paragraph 3: ask to be considered for their vendor list and enquiries, and ask who handles vendor registration "
    "(name or email) or how to register. No prices, quotations, discounts, delivery promises or guarantees; no "
    "flattery or buzzwords. Do not write a greeting, a closing, a signature, or any line about attachments. "
    "Reply with JSON only: {\"subject\": \"...\", \"body\": \"...\"}")


def _context(con, contact):
    comp = con.execute("SELECT * FROM companies WHERE id=?", (contact["company_id"],)).fetchone()
    proj = None
    if comp:
        proj = con.execute("SELECT * FROM projects WHERE lower(client) LIKE ? ORDER BY id DESC LIMIT 1",
                           ("%" + comp["name"].lower()[:20] + "%",)).fetchone()
    return comp, proj


def _assemble(body):
    body = body.strip()
    if settings.brochure_path:
        body += "\n\nOur company brochure is attached for your reference."
    text = "Dear Sir,\n\n" + body + "\n\n" + P.UNSUB_LINE + "\n\n" + P.signature_text()
    h = ("<p>Dear Sir,</p>" + "".join(f"<p>{html.escape(par)}</p>" for par in body.split("\n\n")) +
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
