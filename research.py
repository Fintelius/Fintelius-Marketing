"""Research agents: new projects, prospect companies, public business contacts.
Rule for contacts: only addresses that appear on a cited public page. Never guessed from name patterns."""
import json
from pathlib import Path
import db
import llm
import guard
import profile as P

BASE = Path(__file__).resolve().parent
TARGETS = json.loads((BASE / "targets.json").read_text())

SYS = ("You are a market research assistant for an Indian heat exchanger and pressure vessel manufacturer. "
       "Use web search. Report only facts found on pages you actually read. Never invent. "
       "Reply with JSON only, no commentary.")


def todays_target(day_index):
    return TARGETS[day_index % len(TARGETS)]


def find_projects(con, target, max_new=8):
    q = (f"Find {max_new} recent or upcoming projects (announced in the last 12 months) in {target['industry']} in "
         f"{', '.join(target['regions'])} that would need equipment such as {', '.join(target['products'])}. "
         "Prefer projects at tender, FEED, EPC award or construction stage. "
         'Return a JSON array of objects with keys: name, client, location, stage, source_url, summary (one sentence).')
    data = llm.ask_json(SYS, q, web=True, max_searches=6, max_tokens=3500)
    n = 0
    for p in data if isinstance(data, list) else []:
        if not isinstance(p, dict) or not p.get("name") or not p.get("source_url"):
            continue
        if db.add_project(con, p["name"], p.get("client", ""), location=p.get("location"), stage=p.get("stage"),
                          source_url=p.get("source_url"), summary=p.get("summary"), industry=target["industry"]):
            n += 1
    db.log(con, "research_projects", f"{target['industry']}: {n} new")
    return n


def find_prospects(con, target, max_new=8):
    q = (f"Find {max_new} companies (end users, EPC, EPCM, PMC or LSTK contractors) active in {target['industry']} in "
         f"{', '.join(target['regions'])} that buy or specify {', '.join(target['products'])}. "
         "Include their official website. "
         'Return a JSON array of objects with keys: name, type (end user/EPC/EPCM/PMC/LSTK), country, website, why (one sentence).')
    data = llm.ask_json(SYS, q, web=True, max_searches=6, max_tokens=3000)
    n = 0
    for c in data if isinstance(data, list) else []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        before = con.execute("SELECT count(*) c FROM companies").fetchone()["c"]
        db.upsert_company(con, c["name"], type=c.get("type"), industry=target["industry"],
                          country=c.get("country"), website=c.get("website"), notes=c.get("why"), source="research")
        after = con.execute("SELECT count(*) c FROM companies").fetchone()["c"]
        n += after - before
    db.log(con, "research_prospects", f"{target['industry']}: {n} new")
    return n


def find_contacts(con, company, max_contacts=3):
    q = (f"For the company '{company['name']}' ({company['website'] or 'website unknown'}), find named people in "
         "procurement, projects, piping/static equipment, heat exchanger or mechanical engineering roles, with their "
         "work email addresses IF the address is published on a public page (company site, tender notice, LinkedIn "
         "profile text, press release). Do NOT guess or construct email addresses. Skip any person without a published "
         'address. Return a JSON array (may be empty) of objects with keys: name, designation, department, email, phone, '
         'source_url. Maximum %d.' % max_contacts)
    data = llm.ask_json(SYS, q, web=True, max_searches=5, max_tokens=2500)
    n = 0
    for c in data if isinstance(data, list) else []:
        if not isinstance(c, dict):
            continue
        email = (c.get("email") or "").strip().lower()
        if not guard.is_valid_email(email) or not c.get("source_url"):
            continue
        cid = db.add_contact(con, company["id"], email, name=c.get("name"), phone=c.get("phone"),
                             designation=c.get("designation"), department=c.get("department"),
                             source=c.get("source_url"))
        if cid:
            n += 1
    db.log(con, "research_contacts", f"{company['name']}: {n} new")
    return n


def companies_needing_contacts(con, limit):
    return con.execute(
        "SELECT * FROM companies WHERE id NOT IN (SELECT DISTINCT company_id FROM contacts WHERE company_id IS NOT NULL) "
        "AND id NOT IN (SELECT company_id FROM vendor_regs WHERE status='no_contacts_found') "
        "ORDER BY id LIMIT ?", (limit,)).fetchall()
