"""Organised records: folders by region, then company, plus Excel logs. Rebuilt from the database each day.

Fintelius Marketing/
  00 Master Log.xlsx
  01 India/ Region Log.xlsx
            Larsen & Toubro/ 01 Company Info ... 06 Offers
"""
import re
from pathlib import Path
import db
import research
from config import settings

SUBFOLDERS = ["01 Company Info", "02 Contacts", "03 Correspondence", "04 Vendor Registration",
              "05 Tenders and Enquiries", "06 Offers"]


def safe(name):
    return re.sub(r'[<>:"/\\\\|?*]', "-", (name or "Unknown")).strip(" .")[:80] or "Unknown"


def root():
    return settings.data_dir / "Fintelius Marketing"


def region_dir(r):
    return root() / f"{r['no']:02d} {safe(r['name'])}"


def company_dir(r, company_name):
    return region_dir(r) / safe(company_name)


def ensure_tree(con):
    root().mkdir(parents=True, exist_ok=True)
    for r in research.REGIONS:
        region_dir(r).mkdir(exist_ok=True)
    for c in con.execute("SELECT name, region FROM companies").fetchall():
        try:
            r = research.region(c["region"] or "Rest of World")
        except ValueError:
            continue
        d = company_dir(r, c["name"])
        for s in SUBFOLDERS:
            (d / s).mkdir(parents=True, exist_ok=True)


def write_company_files(con):
    """Company info and contacts as small text files, and every email sent or received as one file each."""
    for c in con.execute("SELECT * FROM companies").fetchall():
        try:
            r = research.region(c["region"] or "Rest of World")
        except ValueError:
            continue
        d = company_dir(r, c["name"])
        info = [f"Company: {c['name']}", f"Type: {c['type'] or ''}", f"Country: {c['country'] or ''}",
                f"Industry: {c['industry'] or ''}", f"Website: {c['website'] or ''}", f"Region: {c['region'] or ''}",
                f"Notes: {c['notes'] or ''}", f"Found: {c['created_at'] or ''}"]
        (d / "01 Company Info" / "company.txt").write_text("\n".join(info), encoding="utf-8")
        rows = con.execute("SELECT * FROM contacts WHERE company_id=?", (c["id"],)).fetchall()
        lines = [f"{k['name'] or ''} | {k['designation'] or ''} | {k['department'] or ''} | {k['email']} | "
                 f"{k['phone'] or ''} | status {k['status']} | source {k['source'] or ''}" for k in rows]
        (d / "02 Contacts" / "contacts.txt").write_text("\n".join(lines), encoding="utf-8")
        for e in con.execute(
                "SELECT e.*, k.email AS to_email FROM emails e JOIN contacts k ON k.id=e.contact_id "
                "WHERE k.company_id=? AND e.status IN ('sent','approved','draft')", (c["id"],)).fetchall():
            stamp = (e["sent_at"] or e["created_at"] or "")[:10]
            fn = d / "03 Correspondence" / f"{stamp} {e['kind']} to {safe(e['to_email'])} ({e['status']}) #{e['id']}.txt"
            fn.write_text(f"To: {e['to_email']}\nSubject: {e['subject']}\nStatus: {e['status']}\n\n{e['body']}",
                          encoding="utf-8")
        ports = con.execute("SELECT * FROM portals WHERE company_id=?", (c["id"],)).fetchall()
        reg = [f"{p['kind']} | {p['url']} | {p['status']} | found {p['found_at']}" for p in ports]
        (d / "04 Vendor Registration" / "portals.txt").write_text("\n".join(reg), encoding="utf-8")


def _sheet(ws, headers, rows):
    from openpyxl.styles import Font
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append(list(r))
    for i, h in enumerate(headers, 1):
        width = max([len(str(h))] + [len(str(r[i - 1] or "")) for r in rows[:300]])
        ws.column_dimensions[ws.cell(1, i).column_letter].width = min(max(width + 2, 10), 70)
    ws.freeze_panes = "A2"


def write_logs(con):
    from openpyxl import Workbook
    root().mkdir(parents=True, exist_ok=True)
    rows = con.execute("SELECT ts,region,company,agent,action,result,source FROM log ORDER BY id").fetchall()
    wb = Workbook()
    ws = wb.active
    ws.title = "Activity log"
    _sheet(ws, ["Date and time (UTC)", "Region", "Company", "Agent", "Action", "Result", "Source link"],
           [tuple(r) for r in rows])
    wb.save(root() / "00 Master Log.xlsx")
    for r in research.REGIONS:
        wb = Workbook()
        ws = wb.active
        ws.title = "Companies"
        comp = con.execute("SELECT name,type,country,website,industry,created_at FROM companies WHERE region=? "
                           "ORDER BY name", (r["name"],)).fetchall()
        _sheet(ws, ["Company", "Type", "Country", "Website", "Industry", "Found"], [tuple(x) for x in comp])
        ws2 = wb.create_sheet("Contacts")
        cont = con.execute(
            "SELECT c.name, k.name, k.designation, k.department, k.email, k.phone, k.status, k.source "
            "FROM contacts k JOIN companies c ON c.id=k.company_id WHERE c.region=? ORDER BY c.name",
            (r["name"],)).fetchall()
        _sheet(ws2, ["Company", "Person", "Designation", "Department", "Email", "Phone", "Status", "Source"],
               [tuple(x) for x in cont])
        ws3 = wb.create_sheet("Projects")
        pr = con.execute("SELECT name,client,location,stage,summary,source_url,found_at FROM projects WHERE region=? "
                         "ORDER BY id DESC", (r["name"],)).fetchall()
        _sheet(ws3, ["Project", "Client", "Location", "Stage", "Summary", "Source", "Found"], [tuple(x) for x in pr])
        ws4 = wb.create_sheet("Portals")
        po = con.execute("SELECT c.name, p.kind, p.url, p.status, p.found_at FROM portals p JOIN companies c "
                         "ON c.id=p.company_id WHERE c.region=? ORDER BY c.name", (r["name"],)).fetchall()
        _sheet(ws4, ["Company", "Kind", "Link", "Status", "Found"], [tuple(x) for x in po])
        ws5 = wb.create_sheet("Activity")
        lg = con.execute("SELECT ts,company,agent,action,result,source FROM log WHERE region=? ORDER BY id",
                         (r["name"],)).fetchall()
        _sheet(ws5, ["Date and time (UTC)", "Company", "Agent", "Action", "Result", "Source"], [tuple(x) for x in lg])
        region_dir(r).mkdir(parents=True, exist_ok=True)
        wb.save(region_dir(r) / "Region Log.xlsx")


def refresh(con):
    ensure_tree(con)
    write_company_files(con)
    write_logs(con)
    return root()
