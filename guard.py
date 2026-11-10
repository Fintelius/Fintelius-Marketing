"""Guard rules. Nothing is sent unless every rule here passes. Stops itself on trouble."""
import re
import datetime as dt
from zoneinfo import ZoneInfo
from config import settings

# Warm-up: emails per day by days since the first send
WARMUP = [(7, 10), (14, 20), (21, 30), (28, 40)]
FINAL_CAP = 50
BOUNCE_STOP = 0.03        # stop if more than 3% of recent sends bounced
BOUNCE_MIN_SAMPLE = 20
SEND_START, SEND_END = (9, 30), (17, 30)   # IST window, weekdays only

ROLE_PREFIXES = {
    "info", "sales", "contact", "admin", "support", "hello", "office", "enquiry", "enquiries", "inquiry",
    "mail", "marketing", "hr", "accounts", "account", "billing", "careers", "jobs", "help", "service",
    "webmaster", "noreply", "no-reply", "donotreply", "postmaster", "procurement", "purchase", "tender",
    "tenders", "vendor", "vendors", "contracts", "enquire",
}
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
PRICE_RE = re.compile(
    r"(₹|\brs\.?\s?\d|\binr\b|\busd\b|\$\s?\d|\beur\b|\bprice[sd]?\b|\bquot(e|ation)\b|\bdiscount|\bcheapest\b|"
    r"\blowest\b|\bper\s+(kg|ton|unit)\b|\bcost\s+of\b)", re.I)
COMMIT_RE = re.compile(
    r"(\bguarantee|\bwe\s+will\s+deliver\b|\bdelivery\s+in\s+\d|\bwithin\s+\d+\s+(days|weeks)\b|"
    r"\bfixed\s+price\b|\bbinding\b|\bwe\s+commit\b|\bpenalt)", re.I)


def is_valid_email(e):
    return bool(e and EMAIL_RE.match(e.strip()))


def is_role_address(e):
    local = e.split("@")[0].lower()
    return local in ROLE_PREFIXES


def check_draft(subject, body):
    """Returns a list of problems. Empty list = OK to send."""
    problems = []
    text = f"{subject}\n{body}"
    if PRICE_RE.search(text):
        problems.append("mentions price/quote")
    if COMMIT_RE.search(text):
        problems.append("makes a commitment")
    if not subject or len(subject) > 120:
        problems.append("bad subject")
    if len(body) < 200 or len(body) > 2500:
        problems.append("body length out of range")
    if not body.lstrip().startswith("Dear Sir,"):
        problems.append("must open with 'Dear Sir,'")
    return problems


def ist_now():
    return dt.datetime.now(ZoneInfo(settings.tz))


def in_send_window(now=None):
    now = now or ist_now()
    if now.weekday() >= 5:
        return False
    t = (now.hour, now.minute)
    return SEND_START <= t < SEND_END


def is_halted():
    return settings.halt_file.exists()


def halt(reason):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.halt_file.write_text(reason)


def resume():
    if settings.halt_file.exists():
        settings.halt_file.unlink()


def first_send_date(con):
    r = con.execute("SELECT min(sent_at) m FROM emails WHERE status='sent'").fetchone()
    return r["m"]


def daily_cap(con, today=None):
    first = first_send_date(con)
    if not first:
        return WARMUP[0][1]
    d0 = dt.datetime.strptime(first, "%Y-%m-%d %H:%M:%S").date()
    today = today or dt.datetime.now(dt.timezone.utc).date()
    days = (today - d0).days
    for limit_days, cap in WARMUP:
        if days < limit_days:
            return cap
    return FINAL_CAP


def sent_today(con):
    # sent_at is stored in UTC; IST day boundary matters little for the cap, UTC day is used consistently
    r = con.execute("SELECT count(*) c FROM emails WHERE status='sent' AND date(sent_at)=date('now')").fetchone()
    return r["c"]


def bounce_rate(con):
    rows = con.execute("SELECT contact_id FROM emails WHERE status='sent' ORDER BY id DESC LIMIT 50").fetchall()
    n = len(rows)
    if n == 0:
        return 0.0, 0
    ids = [r["contact_id"] for r in rows]
    q = ",".join("?" * len(ids))
    b = con.execute(f"SELECT count(*) c FROM contacts WHERE id IN ({q}) AND status='bounced'", ids).fetchone()["c"]
    return b / n, n


def check_bounces_and_halt(con):
    rate, n = bounce_rate(con)
    if n >= BOUNCE_MIN_SAMPLE and rate > BOUNCE_STOP:
        halt(f"Bounce rate {rate:.1%} over last {n} sends. Sending stopped for safety.")
        return True
    return False


def can_send_to(con, contact):
    """Last check for one contact. Returns (ok, reason)."""
    from db import is_dnc
    e = contact["email"]
    if not is_valid_email(e):
        return False, "invalid address"
    if is_dnc(con, e):
        return False, "do-not-contact"
    if is_role_address(e):
        return False, "generic mailbox (needs your approval)"
    if contact["status"] in ("bounced", "dnc", "replied"):
        return False, f"status {contact['status']}"
    return True, ""
