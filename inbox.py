"""Reads the marketing mailbox: bounces, replies, opt-outs. Never answers anything by itself."""
import re
import datetime as dt
import db
import graph

BOUNCE_FROM = ("mailer-daemon", "postmaster", "microsoftexchange", "mail delivery subsystem")
BOUNCE_SUBJ = ("undeliverable", "delivery status notification", "delivery has failed", "returned mail",
               "mail delivery failed", "failure notice", "address not found")
AUTO_SUBJ = ("automatic reply", "auto reply", "auto-reply", "out of office", "autoreply")
OPTOUT = re.compile(r"\b(unsubscribe|not relevant|do not contact|don't contact|stop (sending|emailing)|remove me|"
                    r"remove us|no longer interested|not interested)\b", re.I)
EMAIL_FIND = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def process(con, days=3):
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    msgs = graph.list_inbox(since)
    stats = {"bounced": 0, "replied": 0, "optout": 0, "auto": 0, "other": 0, "needs_you": []}
    for m in msgs:
        mid = m["id"]
        if con.execute("SELECT 1 FROM processed_msgs WHERE id=?", (mid,)).fetchone():
            continue
        con.execute("INSERT INTO processed_msgs(id,ts) VALUES(?,?)", (mid, db.now()))
        subj = (m.get("subject") or "")
        sl = subj.lower()
        frm = ((m.get("from") or {}).get("emailAddress") or {})
        addr = (frm.get("address") or "").lower()
        body = ((m.get("body") or {}).get("content") or m.get("bodyPreview") or "")
        preview = (m.get("bodyPreview") or "")[:300]

        if any(x in addr for x in BOUNCE_FROM) or any(x in sl for x in BOUNCE_SUBJ):
            failed = {e.lower() for e in EMAIL_FIND.findall(body)}
            for e in failed:
                row = con.execute("SELECT id FROM contacts WHERE lower(email)=?", (e,)).fetchone()
                if row:
                    con.execute("UPDATE contacts SET status='bounced' WHERE id=?", (row["id"],))
                    db.add_dnc(con, e, "bounce")
                    stats["bounced"] += 1
            continue
        if any(x in sl for x in AUTO_SUBJ):
            stats["auto"] += 1
            continue
        row = con.execute("SELECT * FROM contacts WHERE lower(email)=?", (addr,)).fetchone()
        if row is None:
            # reply from a colleague or different address: keep for the human
            stats["other"] += 1
            stats["needs_you"].append(f"From {addr}: {subj} - {preview}")
            db.log(con, "inbox_other", f"{addr} | {subj}")
            continue
        if OPTOUT.search(preview + " " + subj):
            db.add_dnc(con, addr, "opt-out reply")
            stats["optout"] += 1
            continue
        con.execute("UPDATE contacts SET status='replied' WHERE id=?", (row["id"],))
        stats["replied"] += 1
        stats["needs_you"].append(f"REPLY from {row['name'] or addr} ({addr}): {subj} - {preview}")
        db.log(con, "reply", f"{addr} | {subj}")
    con.commit()
    return stats
