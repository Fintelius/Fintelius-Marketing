"""SQLite database: the module's memory. One file, easy to back up."""
import sqlite3
import datetime as dt
from config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT, industry TEXT, country TEXT,
  website TEXT, notes TEXT, source TEXT, created_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_company_name ON companies(lower(name));
CREATE TABLE IF NOT EXISTS contacts(
  id INTEGER PRIMARY KEY, company_id INTEGER, name TEXT, email TEXT NOT NULL, phone TEXT,
  designation TEXT, department TEXT, source TEXT, status TEXT DEFAULT 'new', created_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_contact_email ON contacts(lower(email));
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY, name TEXT, client TEXT, location TEXT, stage TEXT,
  source_url TEXT, summary TEXT, industry TEXT, found_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_project ON projects(lower(name), lower(coalesce(client,'')));
CREATE TABLE IF NOT EXISTS emails(
  id INTEGER PRIMARY KEY, contact_id INTEGER, kind TEXT, subject TEXT, body TEXT,
  status TEXT DEFAULT 'draft', problems TEXT, created_at TEXT, sent_at TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS dnc(email TEXT PRIMARY KEY, reason TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS vendor_regs(
  id INTEGER PRIMARY KEY, company_id INTEGER UNIQUE, portal_url TEXT, status TEXT DEFAULT 'to_prepare',
  needs_you TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS processed_msgs(id TEXT PRIMARY KEY, ts TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, detail TEXT);
"""


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect():
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.db_path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def log(con, kind, detail=""):
    con.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)", (now(), kind, str(detail)[:2000]))
    con.commit()


def _insert(con, table, base, extra, allowed):
    """Insert base + allowed extra fields, plus created timestamp column handled by caller."""
    cols = dict(base)
    cols.update({k: v for k, v in extra.items() if k in allowed and v})
    names = ",".join(cols)
    marks = ",".join("?" * len(cols))
    cur = con.execute(f"INSERT INTO {table}({names}) VALUES({marks})", tuple(cols.values()))
    con.commit()
    return cur.lastrowid


def is_dnc(con, email):
    return con.execute("SELECT 1 FROM dnc WHERE email=?", (email.lower(),)).fetchone() is not None


def add_dnc(con, email, reason):
    con.execute("INSERT OR REPLACE INTO dnc(email,reason,ts) VALUES(?,?,?)", (email.lower(), reason, now()))
    con.execute("UPDATE contacts SET status='dnc' WHERE lower(email)=?", (email.lower(),))
    con.commit()


def upsert_company(con, name, **f):
    name = (name or "").strip()
    if not name:
        return None
    row = con.execute("SELECT id FROM companies WHERE lower(name)=?", (name.lower(),)).fetchone()
    if row:
        for k, v in f.items():
            if v and k in ("type", "industry", "country", "website", "notes", "source"):
                con.execute(f"UPDATE companies SET {k}=? WHERE id=? AND coalesce({k},'')=''", (v, row["id"]))
        con.commit()
        return row["id"]
    return _insert(con, "companies", {"name": name, "created_at": now()}, f,
                   ("type", "industry", "country", "website", "notes", "source"))


def add_contact(con, company_id, email, **f):
    """Returns new contact id, or None if the address already exists or is on the do-not-contact list."""
    email = (email or "").strip().lower()
    if not email or is_dnc(con, email):
        return None
    if con.execute("SELECT 1 FROM contacts WHERE lower(email)=?", (email,)).fetchone():
        return None
    return _insert(con, "contacts", {"company_id": company_id, "email": email, "created_at": now()}, f,
                   ("name", "phone", "designation", "department", "source"))


def add_project(con, name, client="", **f):
    try:
        _insert(con, "projects", {"name": name, "client": client, "found_at": now()}, f,
                ("location", "stage", "source_url", "summary", "industry"))
        return True
    except sqlite3.IntegrityError:
        return False
