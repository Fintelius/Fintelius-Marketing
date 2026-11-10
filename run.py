#!/usr/bin/env python3
"""Fintelius marketing module - command line.  Usage: python run.py <command>"""
import argparse
import csv
import datetime as dt
import sys
import db
import guard
import graph
import inbox
import research
import sender
import vendor
import writer
import digest
from config import settings


def cmd_init(a, con):
    print("Database ready at", settings.db_path)


def cmd_check(a, con):
    print("Anthropic key set:", bool(settings.anthropic_api_key))
    print("Mailbox:", settings.mailbox, "| mode:", settings.send_mode)
    print("Mail connection:", *graph.check_connection())


def cmd_import_csv(a, con):
    """Load an existing contact CSV. Needs a column named email; optional name, company, designation, phone, department."""
    n = d = 0
    with open(a.file, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            email = r.get("email") or r.get("e-mail") or ""
            if not guard.is_valid_email(email):
                continue
            cid = db.upsert_company(con, r.get("company") or r.get("organisation") or "Unknown", source="import")
            res = db.add_contact(con, cid, email, name=r.get("name"), phone=r.get("phone") or r.get("mobile"),
                                 designation=r.get("designation"), department=r.get("department"), source="import")
            n += 1 if res else 0
            d += 0 if res else 1
    print(f"Imported {n} new contacts, skipped {d} (duplicates or do-not-contact).")


def cmd_research(a, con):
    day = dt.date.today().toordinal()
    t = research.todays_target(day)
    steps = []
    for label, fn in (("projects", lambda: research.find_projects(con, t)),
                      ("prospects", lambda: research.find_prospects(con, t))):
        try:
            steps.append(f"{t['industry']}: {fn()} new {label}")
        except Exception as e:
            db.log(con, "research_error", f"{label}: {e}")
            steps.append(f"{label} search failed: {str(e)[:120]}")
    got = 0
    for c in research.companies_needing_contacts(con, a.companies):
        try:
            k = research.find_contacts(con, c)
        except Exception as e:
            db.log(con, "research_error", f"{c['name']}: {e}")
            continue
        got += k
        if k == 0:
            con.execute("INSERT OR IGNORE INTO vendor_regs(company_id,status,updated_at) VALUES(?,?,?)",
                        (c["id"], "no_contacts_found", db.now()))
            con.commit()
    steps.append(f"{got} new contacts with published emails")
    return steps


def cmd_prepare(a, con):
    rows = con.execute(
        "SELECT * FROM contacts WHERE status='new' AND id NOT IN (SELECT contact_id FROM emails WHERE kind='intro') "
        "ORDER BY id LIMIT ?", (a.limit,)).fetchall()
    n = 0
    for c in rows:
        ok, why = guard.can_send_to(con, c)
        if not ok:
            continue
        try:
            if writer.draft_intro(con, c):
                n += 1
        except Exception as e:
            db.log(con, "draft_error", f"{c['email']}: {e}")
    f = writer.draft_followups(con)
    return [f"{n} intro drafts, {f} follow-up drafts prepared"]


def cmd_show(a, con):
    e = con.execute("SELECT e.*, c.email, c.name FROM emails e JOIN contacts c ON c.id=e.contact_id WHERE e.id=?",
                    (a.id,)).fetchone()
    if not e:
        sys.exit("No such email")
    print(f"To: {e['name']} <{e['email']}>\nSubject: {e['subject']}\nStatus: {e['status']} {e['problems'] or ''}\n\n{e['body']}")


def cmd_approve(a, con):
    if not (a.all or a.id):
        sys.exit("Use --all or --id N")
    print(sender.approve(con, a.id, a.all), "approved")


def cmd_reject(a, con):
    con.execute("UPDATE emails SET status='rejected' WHERE id=? AND status IN ('draft','approved','blocked')", (a.id,))
    con.commit()


def cmd_send(a, con):
    r = sender.run(con, dry_run=a.dry_run)
    print(r)
    return r


def cmd_inbox(a, con):
    s = inbox.process(con)
    print({k: v for k, v in s.items() if k != "needs_you"})
    return s


def cmd_daily(a, con):
    if guard.is_halted():
        print("HALT file present - nothing will be sent.")
    steps, stats = [], None
    try:
        stats = inbox.process(con)
        steps.append(f"Inbox: {stats['replied']} replies, {stats['bounced']} bounces, {stats['optout']} opt-outs")
    except Exception as e:
        db.log(con, "inbox_error", e)
        steps.append("Inbox check failed: " + str(e)[:120])
    if settings.anthropic_api_key:
        a.companies = 6
        a.limit = 15
        steps += cmd_research(a, con)
        steps += cmd_prepare(a, con)
    steps.append(f"{vendor.prepare(con)} vendor-registration items prepared")
    if settings.send_mode == "auto":
        steps.append("Send: " + str(sender.run(con)))
    try:
        digest.send(con, stats, steps)
    except Exception as e:
        db.log(con, "digest_error", e)
        print("Digest failed:", e)


def cmd_status(a, con):
    for t in ("companies", "contacts", "projects", "emails", "dnc"):
        print(t, con.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
    for r in con.execute("SELECT status, count(*) c FROM emails GROUP BY status"):
        print("  emails", r["status"], r["c"])
    print("halted:", guard.is_halted(), "| cap today:", guard.daily_cap(con), "| sent today:", guard.sent_today(con))


def main():
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd", required=True)
    for name in ("init", "check", "status", "inbox", "daily", "prepare", "research", "send", "stop", "resume"):
        s = sp.add_parser(name)
        if name == "prepare":
            s.add_argument("--limit", type=int, default=15)
        if name == "research":
            s.add_argument("--companies", type=int, default=6)
        if name == "send":
            s.add_argument("--dry-run", action="store_true")
    s = sp.add_parser("import-csv"); s.add_argument("file")
    s = sp.add_parser("show"); s.add_argument("id", type=int)
    s = sp.add_parser("approve"); s.add_argument("--all", action="store_true"); s.add_argument("--id", type=int)
    s = sp.add_parser("reject"); s.add_argument("id", type=int)
    a = p.parse_args()
    con = db.connect()
    if a.cmd == "stop":
        guard.halt("Stopped by owner"); return print("Sending stopped.")
    if a.cmd == "resume":
        guard.resume(); return print("Sending can resume.")
    fn = {"init": cmd_init, "check": cmd_check, "import-csv": cmd_import_csv, "research": cmd_research,
          "prepare": cmd_prepare, "show": cmd_show, "approve": cmd_approve, "reject": cmd_reject,
          "send": cmd_send, "inbox": cmd_inbox, "daily": cmd_daily, "status": cmd_status}[a.cmd]
    out = fn(a, con)
    if a.cmd in ("research", "prepare") and out:
        print("\n".join(out))


if __name__ == "__main__":
    main()
