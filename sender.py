"""Sending step. Every send passes the guard rules first. Random gaps between emails."""
import random
import time
import db
import graph
import guard
import writer
import profile as P


def approve(con, email_id=None, all_drafts=False):
    if all_drafts:
        cur = con.execute("UPDATE emails SET status='approved' WHERE status='draft'")
    else:
        cur = con.execute("UPDATE emails SET status='approved' WHERE id=? AND status='draft'", (email_id,))
    con.commit()
    return cur.rowcount


def run(con, dry_run=False, max_gap=(60, 180), sleep=time.sleep):
    result = {"sent": 0, "skipped": [], "stopped": None}
    if guard.is_halted():
        result["stopped"] = "HALT file present: " + guard.settings.halt_file.read_text()[:200]
        return result
    if guard.check_bounces_and_halt(con):
        result["stopped"] = "bounce rate too high"
        return result
    if not dry_run and not guard.in_send_window():
        result["stopped"] = "outside send window (weekdays 09:30-17:30 IST)"
        return result
    if guard.settings.send_mode == "auto":
        con.execute("UPDATE emails SET status='approved' WHERE status='draft'")
        con.commit()

    cap = guard.daily_cap(con)
    room = cap - guard.sent_today(con)
    rows = con.execute(
        "SELECT e.*, c.email AS to_email FROM emails e JOIN contacts c ON c.id=e.contact_id "
        "WHERE e.status='approved' ORDER BY e.kind='followup' DESC, e.id LIMIT ?", (max(room, 0),)).fetchall()
    for e in rows:
        contact = con.execute("SELECT * FROM contacts WHERE id=?", (e["contact_id"],)).fetchone()
        ok, why = guard.can_send_to(con, contact)
        problems = guard.check_draft(e["subject"], e["body"]) if e["kind"] == "intro" else []
        if not ok or problems:
            reason = why or "; ".join(problems)
            con.execute("UPDATE emails SET status='blocked', problems=? WHERE id=?", (reason, e["id"]))
            con.commit()
            result["skipped"].append(f"{e['to_email']}: {reason}")
            continue
        if dry_run:
            result["sent"] += 1
            continue
        try:
            graph.send_mail(e["to_email"], e["subject"], writer.html_for(e), reply_to=P.REPLY_TO)
            con.execute("UPDATE emails SET status='sent', sent_at=? WHERE id=?", (db.now(), e["id"]))
            con.execute("UPDATE contacts SET status='emailed' WHERE id=? AND status='new'", (e["contact_id"],))
            con.commit()
            result["sent"] += 1
        except Exception as ex:
            con.execute("UPDATE emails SET status='failed', error=? WHERE id=?", (str(ex)[:300], e["id"]))
            con.commit()
            db.log(con, "send_error", str(ex))
            result["stopped"] = "send error: " + str(ex)[:150]
            guard.halt("Send error: " + str(ex)[:300])
            break
        sleep(random.randint(*max_gap))
    return result
