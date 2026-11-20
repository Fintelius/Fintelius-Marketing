"""Registered client enquiries (FP numbers) and the hand-off between the marketing agents and the
enquiry registration agent.

- The registration agent (a Claude session using the fintelius-enquiry-registration skill) registers an
  enquiry on fintelius.net, then calls `run.py enquiry add ...` so the marketing module knows about it.
- The inbox monitor calls note_response() when a client writes about a registered FP number, and
  queue_possible() when a marketing contact's reply looks like a new enquiry. Both end up in the daily report
  and in the 'enquiry_queue' table, which the registration agent works through.
- Contacts of a company with an enquiry are set to status 'client': no intro or follow-up marketing mail is
  drafted for them.
"""
import re
import db

FP_RE = re.compile(r"\bFP-?(\d{7})\b", re.I)
ENQ_RE = re.compile(r"\b(rfq|rfp|enquiry|inquiry|request for quotation|please quote|kindly quote|quotation|"
                    r"tender|bid|require|looking for a supplier|price list)\b", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS enquiries(
  id INTEGER PRIMARY KEY, fp_no TEXT UNIQUE, sr TEXT, company_id INTEGER, contact_id INTEGER,
  client_ref TEXT, product TEXT, received_via TEXT, received_on TEXT, due_date TEXT,
  status TEXT DEFAULT 'registered', ack_sent INTEGER DEFAULT 0, docs_uploaded INTEGER DEFAULT 0,
  onedrive_folder TEXT, outlook_folder TEXT, notes TEXT, created_at TEXT, last_activity TEXT);
CREATE TABLE IF NOT EXISTS enquiry_queue(
  id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, sender TEXT, subject TEXT, preview TEXT,
  fp_no TEXT, status TEXT DEFAULT 'open');
"""
STATUSES = ("registered", "costing", "offer_ready", "offered", "negotiation", "won", "lost", "regretted")


def ensure(con):
    con.executescript(SCHEMA)
    con.commit()


def add(con, fp_no, company, region="", country="", contact_name="", email="", designation="", phone="",
        sr="", client_ref="", product="", received_via="", received_on="", due_date="", onedrive_folder="",
        outlook_folder="", ack_sent=0, docs_uploaded=0, notes="", website=""):
    ensure(con)
    fp = "FP-" + FP_RE.search(fp_no).group(1) if FP_RE.search(fp_no) else fp_no
    cid = db.upsert_company(con, company, type="client", country=country, region=region, website=website,
                            source="client enquiry")
    kid = None
    if email:
        row = con.execute("SELECT id FROM contacts WHERE lower(email)=?", (email.lower(),)).fetchone()
        if row:
            kid = row["id"]
        else:
            kid = db.add_contact(con, cid, email, name=contact_name, designation=designation, phone=phone,
                                 source="client enquiry " + fp, verified=1)
        if kid:
            con.execute("UPDATE contacts SET status='client' WHERE id=?", (kid,))
    # every other contact of this company is also a client now: no marketing mail
    con.execute("UPDATE contacts SET status='client' WHERE company_id=? AND status IN ('new','emailed')", (cid,))
    con.execute(
        "INSERT INTO enquiries(fp_no,sr,company_id,contact_id,client_ref,product,received_via,received_on,due_date,"
        "ack_sent,docs_uploaded,onedrive_folder,outlook_folder,notes,created_at,last_activity) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fp_no) DO UPDATE SET "
        "sr=excluded.sr, client_ref=excluded.client_ref, product=excluded.product, due_date=excluded.due_date, "
        "ack_sent=excluded.ack_sent, docs_uploaded=excluded.docs_uploaded, onedrive_folder=excluded.onedrive_folder, "
        "outlook_folder=excluded.outlook_folder, notes=excluded.notes, last_activity=excluded.last_activity",
        (fp, sr, cid, kid, client_ref, product, received_via, received_on, due_date, int(ack_sent),
         int(docs_uploaded), onedrive_folder, outlook_folder, notes, db.now(), db.now()))
    con.execute("UPDATE enquiry_queue SET status='registered', fp_no=? WHERE status='open' AND lower(sender)=?",
                (fp, (email or "").lower()))
    con.commit()
    comp, reg = db.company_label(con, cid)
    db.record(con, "enquiry registration agent", "enquiry registered",
              f"{fp} (Sr {sr}) {product} | client ref {client_ref} | ack sent {bool(ack_sent)} | documents {bool(docs_uploaded)}",
              reg, comp, "https://fintelius.net/inquiries")
    return fp


def update(con, fp_no, status=None, note=""):
    ensure(con)
    row = con.execute("SELECT * FROM enquiries WHERE fp_no=?", (fp_no,)).fetchone()
    if not row:
        raise SystemExit(f"{fp_no} not found")
    if status:
        if status not in STATUSES:
            raise SystemExit("status must be one of: " + ", ".join(STATUSES))
        con.execute("UPDATE enquiries SET status=? WHERE fp_no=?", (status, fp_no))
    if note:
        con.execute("UPDATE enquiries SET notes=coalesce(notes,'')||?, last_activity=? WHERE fp_no=?",
                    ("\n" + db.now()[:10] + ": " + note, db.now(), fp_no))
    con.execute("UPDATE enquiries SET last_activity=? WHERE fp_no=?", (db.now(), fp_no))
    con.commit()
    comp, reg = db.company_label(con, row["company_id"])
    db.record(con, "enquiry registration agent", "enquiry updated", f"{fp_no}: {status or ''} {note}".strip(), reg, comp)


def find_for_message(con, sender, subject):
    """The registered enquiry a message belongs to: by FP number in the subject, else by the sender's company."""
    ensure(con)
    m = FP_RE.search(subject or "")
    if m:
        r = con.execute("SELECT * FROM enquiries WHERE fp_no=?", ("FP-" + m.group(1),)).fetchone()
        if r:
            return r
    return con.execute(
        "SELECT e.* FROM enquiries e JOIN contacts k ON k.company_id=e.company_id WHERE lower(k.email)=? "
        "AND e.status NOT IN ('won','lost','regretted') ORDER BY e.id DESC", ((sender or "").lower(),)).fetchone()


def note_response(con, enq, sender, subject, preview):
    """A client answered about a registered enquiry. Logged, queued for the registration agent, reported daily."""
    con.execute("UPDATE enquiries SET last_activity=? WHERE id=?", (db.now(), enq["id"]))
    con.execute("INSERT INTO enquiry_queue(ts,kind,sender,subject,preview,fp_no) VALUES(?,?,?,?,?,?)",
                (db.now(), "client_response", sender, subject, preview[:300], enq["fp_no"]))
    con.commit()
    comp, reg = db.company_label(con, enq["company_id"])
    db.record(con, "inbox monitor", "client response on enquiry", f"{enq['fp_no']} from {sender}: {subject}", reg, comp)


def looks_like_enquiry(subject, text):
    return bool(ENQ_RE.search((subject or "") + " " + (text or "")[:600]))


def queue_possible(con, sender, subject, preview, company_id=None):
    """A marketing contact replied with what looks like an enquiry: hand it to the registration agent."""
    ensure(con)
    if con.execute("SELECT 1 FROM enquiry_queue WHERE lower(sender)=? AND subject=? AND status='open'",
                   (sender.lower(), subject)).fetchone():
        return
    con.execute("INSERT INTO enquiry_queue(ts,kind,sender,subject,preview) VALUES(?,?,?,?,?)",
                (db.now(), "possible_enquiry", sender, subject, preview[:300]))
    con.commit()
    comp, reg = db.company_label(con, company_id) if company_id else ("", "")
    db.record(con, "inbox monitor", "enquiry detected", f"{sender}: {subject} - sent to registration agent queue",
              reg, comp)


def open_queue(con):
    ensure(con)
    return con.execute("SELECT * FROM enquiry_queue WHERE status='open' ORDER BY id").fetchall()


def active(con):
    ensure(con)
    return con.execute(
        "SELECT e.*, c.name AS company, c.region FROM enquiries e LEFT JOIN companies c ON c.id=e.company_id "
        "WHERE e.status NOT IN ('won','lost','regretted') ORDER BY e.id DESC").fetchall()


def report_lines(con):
    """Lines for the daily report: queue first, then active enquiries with days since last activity."""
    out = []
    for q in open_queue(con):
        out.append(("CLIENT RESPONSE on " + q["fp_no"] if q["kind"] == "client_response" else "POSSIBLE NEW ENQUIRY")
                   + f" from {q['sender']}: {q['subject']}")
    for e in active(con):
        days = con.execute("SELECT cast(julianday('now')-julianday(?) as int)", (e["last_activity"] or e["created_at"],)).fetchone()[0]
        flags = []
        if not e["ack_sent"]:
            flags.append("acknowledgement not sent")
        if not e["docs_uploaded"]:
            flags.append("documents not on record")
        out.append(f"{e['fp_no']} {e['company']} - {e['product'] or ''} [{e['status']}] last activity {days} day(s) ago"
                   + (" | " + ", ".join(flags) if flags else ""))
    return out
