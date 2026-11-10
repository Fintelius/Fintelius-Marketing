"""Daily summary email to the owner."""
import html
import db
import graph
import guard
import vendor
from config import settings


def build(con, inbox_stats=None, steps=None):
    q = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    drafts = con.execute(
        "SELECT e.id, e.kind, e.subject, c.email, c.name FROM emails e JOIN contacts c ON c.id=e.contact_id "
        "WHERE e.status='draft' ORDER BY e.id LIMIT 40").fetchall()
    blocked = con.execute(
        "SELECT e.id, c.email, e.problems FROM emails e JOIN contacts c ON c.id=e.contact_id "
        "WHERE e.status='blocked' ORDER BY e.id DESC LIMIT 10").fetchall()
    rate, n = guard.bounce_rate(con)
    total_sent = con.execute("SELECT count(*) FROM emails WHERE status='sent'").fetchone()[0]
    h = ["<h2>Fintelius marketing - daily report</h2>"]
    if guard.is_halted():
        h.append(f"<p style='color:#b00'><b>SENDING IS STOPPED:</b> {html.escape(settings.halt_file.read_text())}</p>")
    h.append("<ul>")
    h.append(f"<li>Companies: {q('SELECT count(*) FROM companies')} | Contacts: {q('SELECT count(*) FROM contacts')} | "
             f"Projects: {q('SELECT count(*) FROM projects')}</li>")
    h.append(f"<li>Sent today: {guard.sent_today(con)} of {guard.daily_cap(con)} allowed | Total sent: "
             f"{total_sent}</li>")
    h.append(f"<li>Bounce rate (last {n} sends): {rate:.1%} | Mode: {settings.send_mode}</li></ul>")
    if steps:
        h.append("<h3>Today's work</h3><ul>" + "".join(f"<li>{html.escape(str(s))}</li>" for s in steps) + "</ul>")
    if inbox_stats and inbox_stats.get("needs_you"):
        h.append("<h3>Replies and messages needing you</h3><ul>" +
                 "".join(f"<li>{html.escape(x)}</li>" for x in inbox_stats["needs_you"]) + "</ul>")
    if drafts:
        h.append(f"<h3>{len(drafts)} drafts waiting for your approval</h3><p>On the server: "
                 "<code>python run.py show ID</code> to read, <code>python run.py approve --id ID</code> or "
                 "<code>python run.py approve --all</code> to approve.</p><ul>")
        for d in drafts:
            h.append(f"<li>#{d['id']} [{d['kind']}] to {html.escape(d['name'] or '')} &lt;{html.escape(d['email'])}&gt; - "
                     f"{html.escape(d['subject'] or '')}</li>")
        h.append("</ul>")
    if blocked:
        h.append("<h3>Blocked by safety rules</h3><ul>" + "".join(
            f"<li>#{b['id']} {html.escape(b['email'])}: {html.escape(b['problems'] or '')}</li>" for b in blocked) + "</ul>")
    nv = vendor.needs_you(con)
    if nv:
        h.append("<h3>Vendor registration - needs a person</h3><ul>" + "".join(
            f"<li>{html.escape(v['name'])} {html.escape(v['portal_url'] or '')} ({v['status']})</li>" for v in nv) + "</ul>")
    return "".join(h)


def send(con, inbox_stats=None, steps=None):
    graph.send_mail(settings.digest_to, "Fintelius marketing - daily report", build(con, inbox_stats, steps))
