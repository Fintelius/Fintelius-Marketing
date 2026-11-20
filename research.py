"""Regional research agents.

One agent per region (see regions.json). Each agent:
  1. finds projects in its region            -> projects table
  2. finds buyer companies in its region     -> companies table (+ vendor registration / tender portal links)
  3. finds named contacts for new companies  -> contacts table, ONLY if the email is really written on the source page
Nothing is researched twice: known names are passed to the agent as "do not return", the database refuses
duplicates, and a company's contacts are searched only once.
Hand-over: a registration link found here queues the registration agent; a tender portal queues the lead agent.
"""
import json
import re
import datetime as dt
from pathlib import Path
import requests
import db
import llm
import guard

BASE = Path(__file__).resolve().parent
REGIONS = json.loads((BASE / "regions.json").read_text())
INDUSTRIES = ("oil and gas refineries, petrochemicals, fertilisers, power plants, gas processing and LNG, "
              "chemicals, and upstream oil and gas facilities")
PRODUCTS = ("air cooled heat exchangers, shell and tube heat exchangers, tube bundles, finned tubes, "
            "pressure vessels, process skids")
SYS = ("You are a market research assistant for an Indian heat exchanger and pressure vessel manufacturer. "
       "Report only facts found on real web pages, each with its source URL. Never invent companies, people, "
       "email addresses or links. Reply with JSON only, no other text.")
UA = {"User-Agent": "Mozilla/5.0 (compatible; FinteliusResearch/1.0)"}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def region(name):
    for r in REGIONS:
        if r["name"].lower() == name.lower():
            return r
    raise ValueError(f"Unknown region: {name}")


def where(r):
    return ", ".join(r["countries"])


def known_names(con, table, region_name, col="name", limit=150):
    rows = con.execute(f"SELECT {col} FROM {table} WHERE region=? ORDER BY id DESC LIMIT ?",
                       (region_name, limit)).fetchall()
    return [x[0] for x in rows if x[0]]


def due_regions(con, today=None, max_regions=6):
    """Regions whose turn has come, most overdue first, at most max_regions per day."""
    today = today or dt.date.today()
    out = []
    for r in REGIONS:
        row = con.execute("SELECT last_run FROM region_runs WHERE region=?", (r["name"],)).fetchone()
        if row and row["last_run"]:
            days = (today - dt.date.fromisoformat(row["last_run"][:10])).days
            if days < r["every_days"]:
                continue
            overdue = days - r["every_days"]
        else:
            overdue = 999
        out.append((overdue, r["no"], r))
    out.sort(key=lambda x: (-x[0], x[1]))
    return [r for _, _, r in out[:max_regions]]


def mark_run(con, region_name):
    con.execute("INSERT INTO region_runs(region,last_run,runs) VALUES(?,?,1) "
                "ON CONFLICT(region) DO UPDATE SET last_run=excluded.last_run, runs=runs+1",
                (region_name, dt.date.today().isoformat()))
    con.commit()


# ---------- 1. projects ----------
def find_projects(con, r, meter, max_new=6):
    skip = known_names(con, "projects", r["name"])
    q = (f"Region: {where(r)}.\nFind up to {max_new} projects announced or awarded in the last 12 months in "
         f"{INDUSTRIES} that will need equipment such as {PRODUCTS}. Prefer tender, FEED, EPC award or "
         f"construction stage. Do NOT return these already-known projects: {json.dumps(skip[:120])}.\n"
         "Return a JSON array of objects: name, client, epc_contractor, country, location, stage, source_url, "
         "summary (one sentence).")
    data = llm.ask_json(SYS, q, web=True, max_searches=6, max_tokens=3500, meter=meter)
    n = 0
    for p in data if isinstance(data, list) else []:
        if not isinstance(p, dict) or not p.get("name") or not p.get("source_url"):
            continue
        if db.add_project(con, p["name"], p.get("client", ""), location=p.get("location"), stage=p.get("stage"),
                          source_url=p["source_url"], summary=p.get("summary"), region=r["name"],
                          industry=p.get("country")):
            n += 1
            db.record(con, "research", "project found", p.get("stage") or "", r["name"],
                      p.get("client") or "", p["source_url"])
            # the client and the EPC contractor of a project are prospects too
            for who, typ in ((p.get("client"), "end user"), (p.get("epc_contractor"), "EPC")):
                if who and len(who) > 2:
                    db.upsert_company(con, who, type=typ, country=p.get("country"), region=r["name"],
                                      notes=f"Linked to project: {p['name']}", source="project research")
    return n


# ---------- 2. companies + portals ----------
def find_prospects(con, r, meter, max_new=8):
    skip = known_names(con, "companies", r["name"])
    q = (f"Region: {where(r)}.\nFind up to {max_new} companies (end users, EPC, EPCM, PMC or LSTK contractors) "
         f"active in {INDUSTRIES} in this region that buy or specify {PRODUCTS}. Do NOT return these "
         f"already-known companies: {json.dumps(skip[:150])}.\nFor each, look on its official website for a "
         "supplier / vendor registration page and for an e-tender or procurement portal.\n"
         "Return a JSON array of objects: name, type (end user/EPC/EPCM/PMC/LSTK), country, website, why "
         "(one sentence), vendor_registration_url (or empty), tender_portal_url (or empty).")
    data = llm.ask_json(SYS, q, web=True, max_searches=8, max_tokens=3500, meter=meter)
    n = portals = 0
    for c in data if isinstance(data, list) else []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        existed = con.execute("SELECT id FROM companies WHERE lower(name)=?", (c["name"].strip().lower(),)).fetchone()
        cid = db.upsert_company(con, c["name"], type=c.get("type"), country=c.get("country"),
                                website=c.get("website"), notes=c.get("why"), region=r["name"], source="research")
        if not cid:
            continue
        if not existed:
            n += 1
            db.record(con, "research", "company found", c.get("type") or "", r["name"], c["name"], c.get("website"))
        for kind, key, nxt in (("registration", "vendor_registration_url", "registration agent queued"),
                               ("tender", "tender_portal_url", "lead agent queued")):
            url = (c.get(key) or "").strip()
            if url.startswith("http") and db.add_portal(con, cid, kind, url):
                portals += 1
                db.record(con, "research", f"{kind} portal found", nxt, r["name"], c["name"], url)
    return n, portals


# ---------- 3. contacts, verified ----------
def page_has_email(url, email, get=requests.get):
    """True only if the exact address is written on the source page."""
    try:
        resp = get(url, headers=UA, timeout=20)
        if resp.status_code != 200:
            return False, f"source page returned {resp.status_code}"
        text = resp.text.lower()
        text = text.replace("[at]", "@").replace("(at)", "@").replace(" at ", "@")
        return (email.lower() in text), "address not on source page"
    except Exception as e:
        return False, "source page could not be opened"


def find_contacts(con, company, meter, max_contacts=3, get=requests.get):
    q = (f"For the company '{company['name']}' ({company['website'] or 'website unknown'}, "
         f"{company['country'] or ''}), find named people in procurement, purchasing, vendor development, projects, "
         "static equipment, heat exchanger or mechanical engineering roles, with their work email address ONLY if "
         "that exact address is written on a public page (company site, tender notice, press release, "
         "directory). Do NOT guess or build addresses from name patterns. Also return any general procurement or "
         "vendor-registration email written on the company's own site.\nReturn a JSON array (may be empty) of "
         f"objects: name, designation, department, email, phone, source_url. Maximum {max_contacts + 2}.")
    data = llm.ask_json(SYS, q, web=True, max_searches=5, max_tokens=2500, meter=meter)
    n = 0
    region_name = company["region"] or ""
    for c in data if isinstance(data, list) else []:
        if not isinstance(c, dict):
            continue
        email = (c.get("email") or "").strip().lower()
        src = (c.get("source_url") or "").strip()
        if not guard.is_valid_email(email) or not src.startswith("http"):
            continue
        if con.execute("SELECT 1 FROM rejected_contacts WHERE email=?", (email,)).fetchone():
            continue
        ok, why = page_has_email(src, email, get=get)
        if ok and not guard.domain_accepts_mail(email):
            ok, why = False, "email domain does not accept mail"
        if not ok:
            con.execute("INSERT OR IGNORE INTO rejected_contacts(email,reason,source,ts) VALUES(?,?,?,?)",
                        (email, why, src, db.now()))
            con.commit()
            db.record(con, "contact check", "contact rejected", f"{email}: {why}", region_name, company["name"], src)
            continue
        cid = db.add_contact(con, company["id"], email, name=c.get("name"), phone=c.get("phone"),
                             designation=c.get("designation"), department=c.get("department"), source=src,
                             verified=1)
        if cid:
            n += 1
            db.record(con, "contact check", "contact verified", f"{c.get('name') or ''} <{email}>",
                      region_name, company["name"], src)
        if n >= max_contacts:
            break
    con.execute("UPDATE companies SET contacts_searched_at=? WHERE id=?", (db.now(), company["id"]))
    con.commit()
    return n


def companies_needing_contacts(con, region_name, limit):
    return con.execute(
        "SELECT * FROM companies WHERE region=? AND contacts_searched_at IS NULL "
        "AND id NOT IN (SELECT DISTINCT company_id FROM contacts WHERE company_id IS NOT NULL) "
        "ORDER BY id LIMIT ?", (region_name, limit)).fetchall()


# ---------- one regional agent ----------
def run_region(r, companies_per_run=4, get=requests.get):
    """Runs in its own thread with its own database connection. Returns a one-line summary."""
    con = db.connect()
    meter = {}
    name = r["name"]
    parts = []
    try:
        db.record(con, "research", "regional agent started", "", name)
        for label, fn in (("projects", lambda: find_projects(con, r, meter)),
                          ("companies", lambda: find_prospects(con, r, meter))):
            try:
                res = fn()
                if label == "companies":
                    parts.append(f"{res[0]} new companies, {res[1]} portals")
                else:
                    parts.append(f"{res} new projects")
            except Exception as e:
                db.record(con, "research", f"{label} search failed", str(e)[:300], name)
                parts.append(f"{label} search failed")
        got = 0
        for c in companies_needing_contacts(con, name, companies_per_run):
            try:
                got += find_contacts(con, c, meter, get=get)
            except Exception as e:
                db.record(con, "contact check", "contact search failed", str(e)[:300], name, c["name"])
        parts.append(f"{got} verified contacts")
        mark_run(con, name)
        usage = (f"{meter.get('calls', 0)} AI calls, {meter.get('searches', 0)} web searches, "
                 f"{meter.get('in', 0)} in / {meter.get('out', 0)} out tokens")
        db.record(con, "research", "regional agent finished", "; ".join(parts) + " | usage: " + usage, name)
        return f"{name}: " + ", ".join(parts)
    finally:
        con.close()


def assign_regions(con):
    """Gives old companies (from before regions existed) a region from their country."""
    simple = {}
    for r in REGIONS:
        for c in r["countries"]:
            simple[c.lower()] = r["name"]
    simple.update({"usa": "South USA", "united states": "South USA", "uae": "UAE", "ksa": "Saudi Arabia",
                   "uk": "Europe", "oman": "Rest of Middle East", "bahrain": "Rest of Middle East"})
    n = 0
    for row in con.execute("SELECT id, country FROM companies WHERE region IS NULL OR region=''").fetchall():
        reg = simple.get((row["country"] or "").strip().lower(), "Rest of World" if row["country"] else "India")
        con.execute("UPDATE companies SET region=? WHERE id=?", (reg, row["id"]))
        n += 1
    con.commit()
    return n
