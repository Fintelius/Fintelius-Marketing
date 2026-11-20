"""Tender register: tenders found on government / end-user portals, and the portals themselves.

The tender agent (a Claude session using the fintelius-tender-agent skill) scans the portals, downloads the
tender documents, checks whether Fintelius can qualify and records the result here with `run.py tender ...`.
The regional marketing agent finds new portals and adds them with `run.py tender portal-add`.
The agent never submits a bid: Swapnil decides BID / NO BID.
"""
import re
import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS tender_portals(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, url TEXT, region TEXT, kind TEXT, login_status TEXT DEFAULT 'to create',
  login_id TEXT, skill TEXT, last_scan TEXT, notes TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS tenders(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE, portal TEXT, buyer TEXT, title TEXT, product TEXT, closing_date TEXT,
  estimated_value TEXT, status TEXT DEFAULT 'found', verdict TEXT, reasons TEXT, folder TEXT, fp_no TEXT,
  notes TEXT, created_at TEXT, last_activity TEXT);
"""
# found -> downloaded -> checked -> (bid | no_bid | query) -> registered (becomes an FP enquiry) -> submitted -> won/lost
STATUSES = ("found", "downloaded", "checked", "bid", "no_bid", "query", "registered", "submitted", "won", "lost", "expired")
VERDICTS = ("qualify", "not_qualify", "query")
KINDS = ("government", "psu", "end-user", "other")


def ensure(con):
    con.executescript(SCHEMA)
    con.commit()


def _now():
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def portal_add(con, name, url="", region="", kind="", login_status="", login_id="", skill="", notes=""):
    ensure(con)
    con.execute(
        "INSERT INTO tender_portals(name,url,region,kind,login_status,login_id,skill,notes,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET url=COALESCE(NULLIF(excluded.url,''),url), "
        "region=COALESCE(NULLIF(excluded.region,''),region), kind=COALESCE(NULLIF(excluded.kind,''),kind), "
        "login_status=COALESCE(NULLIF(excluded.login_status,''),login_status), "
        "login_id=COALESCE(NULLIF(excluded.login_id,''),login_id), skill=COALESCE(NULLIF(excluded.skill,''),skill), "
        "notes=COALESCE(NULLIF(excluded.notes,''),notes)",
        (name, url, region, kind, login_status or "to create", login_id, skill, notes, _now()))
    con.commit()


def portal_scanned(con, name):
    ensure(con)
    con.execute("UPDATE tender_portals SET last_scan=? WHERE name=?", (_now(), name))
    con.commit()


def add(con, ref, portal="", buyer="", title="", product="", closing_date="", estimated_value="", folder="", notes=""):
    ensure(con)
    con.execute(
        "INSERT INTO tenders(ref,portal,buyer,title,product,closing_date,estimated_value,folder,notes,created_at,last_activity) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ref) DO UPDATE SET title=COALESCE(NULLIF(excluded.title,''),title), "
        "closing_date=COALESCE(NULLIF(excluded.closing_date,''),closing_date), "
        "folder=COALESCE(NULLIF(excluded.folder,''),folder), last_activity=excluded.last_activity",
        (ref, portal, buyer, title, product, closing_date, estimated_value, folder, notes, _now(), _now()))
    con.commit()
    try:
        db.log(con, "tender agent", buyer or portal, "tender found", ref)
    except Exception:
        pass


def update(con, ref, status="", verdict="", reasons="", fp_no="", folder="", note=""):
    ensure(con)
    row = con.execute("SELECT * FROM tenders WHERE ref=?", (ref,)).fetchone()
    if not row:
        raise SystemExit("unknown tender " + ref)
    if status and status not in STATUSES:
        raise SystemExit("status must be one of " + ", ".join(STATUSES))
    if verdict and verdict not in VERDICTS:
        raise SystemExit("verdict must be one of " + ", ".join(VERDICTS))
    notes = (row["notes"] or "") + (f"\n{_now()[:10]}: {note}" if note else "")
    con.execute(
        "UPDATE tenders SET status=COALESCE(NULLIF(?,''),status), verdict=COALESCE(NULLIF(?,''),verdict), "
        "reasons=COALESCE(NULLIF(?,''),reasons), fp_no=COALESCE(NULLIF(?,''),fp_no), "
        "folder=COALESCE(NULLIF(?,''),folder), notes=?, last_activity=? WHERE ref=?",
        (status, verdict, reasons, fp_no, folder, notes, _now(), ref))
    con.commit()


def open_tenders(con):
    ensure(con)
    return con.execute("SELECT * FROM tenders WHERE status NOT IN ('won','lost','expired','no_bid') "
                       "ORDER BY closing_date").fetchall()


def awaiting_decision(con):
    ensure(con)
    return con.execute("SELECT * FROM tenders WHERE status='checked' ORDER BY closing_date").fetchall()


def report_lines(con):
    out = []
    for t in open_tenders(con):
        out.append(f"{t['ref']} | {t['buyer'] or t['portal']} | {t['title'] or t['product']} | closes {t['closing_date'] or '?'} "
                   f"| {t['status']}" + (f" | verdict {t['verdict']}" if t['verdict'] else ""))
    return out


def portal_lines(con):
    ensure(con)
    return [f"{p['name']} | {p['region'] or ''} | login: {p['login_status']} | last scan {p['last_scan'] or 'never'}"
            for p in con.execute("SELECT * FROM tender_portals ORDER BY name")]
