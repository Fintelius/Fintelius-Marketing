"""Vendor registration: the module only PREPARES. A person completes the portal form."""
import db

PACK = [
    "Company profile / brochure (PDF)",
    "GST certificate, PAN, incorporation certificate",
    "Bank details letter and cancelled cheque",
    "MSME / Udyam certificate",
    "ISO 9001 / ASME U-stamp / NB / PED certificates (as held)",
    "Past supply list and customer references",
    "ADNOC AVL number 0020004813 (for Middle East clients)",
    "Authorised signatory details and board resolution",
]


def prepare(con, limit=20):
    """Create a vendor-registration to-do for every company that has replied or asked for registration."""
    n = 0
    rows = con.execute(
        "SELECT DISTINCT c.id, c.name, c.website FROM companies c JOIN contacts k ON k.company_id=c.id "
        "WHERE k.status='replied' AND c.id NOT IN (SELECT company_id FROM vendor_regs WHERE company_id IS NOT NULL) "
        "LIMIT ?", (limit,)).fetchall()
    for r in rows:
        con.execute("INSERT INTO vendor_regs(company_id,portal_url,status,needs_you,updated_at) VALUES(?,?,?,?,?)",
                    (r["id"], r["website"] or "", "to_prepare",
                     "Find the vendor registration page, fill the form and upload: " + "; ".join(PACK), db.now()))
        n += 1
    con.commit()
    return n


def needs_you(con):
    return con.execute(
        "SELECT v.id, c.name, v.portal_url, v.status FROM vendor_regs v JOIN companies c ON c.id=v.company_id "
        "WHERE v.status IN ('to_prepare','prepared') ORDER BY v.id").fetchall()
