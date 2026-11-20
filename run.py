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
import profile as P
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
    """Runs every region that is due, all at the same time (one thread per region)."""
    from concurrent.futures import ThreadPoolExecutor
    research.assign_regions(con)
    if getattr(a, "region", None):
        todo = [research.region(a.region)]
    else:
        todo = research.due_regions(con, max_regions=getattr(a, "max_regions", 6))
    steps = []
    if not todo:
        return ["Research: no region is due today"]
    with ThreadPoolExecutor(max_workers=len(todo)) as pool:
        futures = [(r["name"], pool.submit(research.run_region, r, getattr(a, "companies", 4))) for r in todo]
        for name, f in futures:
            try:
                steps.append(f.result())
            except Exception as e:
                db.log(con, "research_error", f"{name}: {e}")
                steps.append(f"{name}: failed ({str(e)[:100]})")
    return steps


def cmd_prepare(a, con):
    rows = con.execute(
        "SELECT * FROM contacts WHERE status='new' AND id NOT IN (SELECT contact_id FROM emails WHERE kind='intro' AND status!='rejected') "
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


def cmd_test_send(a, con):
    """Send one stored draft to a test address (for example yourself). Changes nothing in the records."""
    e = con.execute("SELECT * FROM emails WHERE id=?", (a.id,)).fetchone()
    if not e:
        sys.exit("No such email")
    graph.send_mail(a.to, "[TEST] " + e["subject"], writer.html_for(e), reply_to=P.REPLY_TO,
                    attachment=settings.brochure_path if e["kind"] == "intro" else None)
    print("Test email sent to", a.to, "| brochure attached:", bool(settings.brochure_path))


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
        a.companies = 4
        a.limit = 15
        a.region = None
        a.max_regions = 6
        steps += cmd_research(a, con)
        steps += cmd_prepare(a, con)
    steps.append(f"{vendor.prepare(con)} vendor-registration items prepared")
    try:
        import archive
        steps.append("Records refreshed: " + str(archive.refresh(con)))
    except Exception as e:
        db.log(con, "archive_error", e)
        steps.append("Records refresh failed: " + str(e)[:100])
    if settings.send_mode == "auto":
        steps.append("Send: " + str(sender.run(con)))
    try:
        digest.send(con, stats, steps)
    except Exception as e:
        db.log(con, "digest_error", e)
        print("Digest failed:", e)


def cmd_records(a, con):
    import archive
    print("Records written to", archive.refresh(con))


def cmd_log(a, con):
    for x in con.execute("SELECT ts,region,company,agent,action,result FROM log ORDER BY id DESC LIMIT ?", (a.n,)):
        print(" | ".join(str(v or "") for v in x))


def cmd_status(a, con):
    for t in ("companies", "contacts", "projects", "emails", "dnc"):
        print(t, con.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
    for r in con.execute("SELECT status, count(*) c FROM emails GROUP BY status"):
        print("  emails", r["status"], r["c"])
    print("halted:", guard.is_halted(), "| cap today:", guard.daily_cap(con), "| sent today:", guard.sent_today(con))


def main():
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd", required=True)
    for name in ("records", "init", "check", "status", "inbox", "daily", "prepare", "research", "send", "stop", "resume"):
        s = sp.add_parser(name)
        if name == "prepare":
            s.add_argument("--limit", type=int, default=15)
        if name == "research":
            s.add_argument("--companies", type=int, default=4)
            s.add_argument("--region", default=None)
            s.add_argument("--max-regions", type=int, default=6, dest="max_regions")
        if name == "send":
            s.add_argument("--dry-run", action="store_true")
    s = sp.add_parser("import-csv"); s.add_argument("file")
    s = sp.add_parser("log"); s.add_argument("n", type=int, nargs="?", default=30)
    s = sp.add_parser("show"); s.add_argument("id", type=int)
    s = sp.add_parser("approve"); s.add_argument("--all", action="store_true"); s.add_argument("--id", type=int)
    s = sp.add_parser("reject"); s.add_argument("id", type=int)
    s = sp.add_parser("test-send"); s.add_argument("id", type=int); s.add_argument("--to", required=True)
    a = p.parse_args()
    con = db.connect()
    if a.cmd == "stop":
        guard.halt("Stopped by owner"); return print("Sending stopped.")
    if a.cmd == "resume":
        guard.resume(); return print("Sending can resume.")
    fn = {"init": cmd_init, "check": cmd_check, "import-csv": cmd_import_csv, "research": cmd_research,
          "prepare": cmd_prepare, "show": cmd_show, "approve": cmd_approve, "reject": cmd_reject, "test-send": cmd_test_send,
          "send": cmd_send, "inbox": cmd_inbox, "daily": cmd_daily, "status": cmd_status, "records": cmd_records, "log": cmd_log}[a.cmd]
    out = fn(a, con)
    if a.cmd in ("research", "prepare") and out:
        print("\n".join(out))


if __name__ == "__main__":
    main()
