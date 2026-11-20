"""Main orchestrator.

Every month it researches where the potential is (one AI web research call covering all 17 regions), then decides for
EACH region how often it is researched and how deep. All regions stay covered: the slowest region is still visited
at least every 7 days. India, the Middle East and the USA (your preferred markets) get extra weight.
Real results (verified contacts, replies) are added to the score, so regions that pay off get more attention.
"""
import json
import datetime as dt
import db
import llm
import research

PREFERRED = {"India", "North USA", "South USA", "UAE", "Kuwait", "Qatar", "Saudi Arabia", "Rest of Middle East"}
PREF_WEIGHT = 1.5
REASSESS_DAYS = 30
MAX_EVERY = 7

SCHEMA = """CREATE TABLE IF NOT EXISTS region_plan(
 region TEXT PRIMARY KEY, potential INTEGER, reason TEXT, source TEXT, assessed_at TEXT,
 score REAL, every_days INTEGER, companies INTEGER, decided_at TEXT)"""

SYS = ("You are a market analyst for an Indian manufacturer of air cooled heat exchangers, shell and tube heat "
       "exchangers, finned tubes, pressure vessels and process skids (ASME U/U2/R, ADNOC approved). Use current web "
       "sources only. Reply with JSON only.")


def _ensure(con):
    con.execute(SCHEMA)
    con.commit()


def assess_potential(con, meter, force=False):
    """One call scoring all regions 1-10 for the next 12 months. Skipped if assessed within REASSESS_DAYS."""
    _ensure(con)
    row = con.execute("SELECT MAX(assessed_at) FROM region_plan").fetchone()
    if row[0] and not force:
        if (dt.date.today() - dt.date.fromisoformat(row[0][:10])).days < REASSESS_DAYS:
            return 0
    names = [r["name"] for r in research.REGIONS]
    q = ("Score each region 1-10 for the sales potential of process heat exchangers and pressure vessels from an "
         "Indian manufacturer over the next 12 months. Base it on current refinery, petrochemical, fertiliser, power "
         "and gas projects, budgets and tenders, import openness to Indian suppliers, and competition. Regions: "
         f"{json.dumps(names)}.\nReturn a JSON array of objects: region (exactly as given), score (integer 1-10), "
         "reason (one sentence with a fact), source_url.")
    data = llm.ask_json(SYS, q, web=True, max_searches=8, max_tokens=3500, meter=meter)
    n = 0
    for d in data if isinstance(data, list) else []:
        if d.get("region") not in names:
            continue
        try:
            s = max(1, min(10, int(d.get("score"))))
        except (TypeError, ValueError):
            continue
        con.execute("INSERT INTO region_plan(region,potential,reason,source,assessed_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(region) DO UPDATE SET potential=excluded.potential, reason=excluded.reason, "
                    "source=excluded.source, assessed_at=excluded.assessed_at",
                    (d["region"], s, str(d.get("reason", ""))[:300], str(d.get("source_url", ""))[:300], db.now()))
        db.record(con, "orchestrator", "market potential assessed", f"{s}/10: {d.get('reason', '')}"[:300],
                  d["region"], "", d.get("source_url"))
        n += 1
    con.commit()
    return n


def performance(con, region_name, days=30):
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    v = con.execute("SELECT COUNT(*) FROM log WHERE region=? AND action='contact verified' AND ts>=?",
                    (region_name, since)).fetchone()[0]
    rep = con.execute("SELECT COUNT(*) FROM log WHERE region=? AND action='reply received' AND ts>=?",
                      (region_name, since)).fetchone()[0]
    return min(5.0, v * 0.5 + rep * 2)


def every_days_for(score):
    if score >= 12:
        return 1
    if score >= 9:
        return 2
    if score >= 6:
        return 3
    if score >= 4:
        return 5
    return MAX_EVERY


def decide(con):
    """Writes the plan: every_days and companies per run for every region. Returns the plan sorted by score."""
    _ensure(con)
    plan = []
    for r in research.REGIONS:
        row = con.execute("SELECT potential FROM region_plan WHERE region=?", (r["name"],)).fetchone()
        pot = row["potential"] if row and row["potential"] else 5   # unknown potential counts as middle
        w = PREF_WEIGHT if r["name"] in PREFERRED else 1.0
        score = round(pot * w + performance(con, r["name"]), 1)
        every = min(every_days_for(score), MAX_EVERY)
        comp = 5 if score >= 12 else 4 if score >= 8 else 3
        con.execute("INSERT INTO region_plan(region,potential,score,every_days,companies,decided_at) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(region) DO UPDATE SET score=excluded.score, every_days=excluded.every_days, "
                    "companies=excluded.companies, decided_at=excluded.decided_at",
                    (r["name"], pot, score, every, comp, db.now()))
        plan.append((score, r["name"], every, comp))
    con.commit()
    plan.sort(reverse=True)
    db.record(con, "orchestrator", "plan decided",
              "; ".join(f"{n}: every {e}d, {c} companies" for _, n, e, c in plan)[:600])
    return plan


def get_plan(con, region_name):
    _ensure(con)
    return con.execute("SELECT every_days, companies FROM region_plan WHERE region=?", (region_name,)).fetchone()


def run(con, meter=None):
    meter = meter if meter is not None else {}
    try:
        assess_potential(con, meter)
    except Exception as e:
        db.record(con, "orchestrator", "potential assessment failed", str(e)[:200])
    plan = decide(con)
    top = ", ".join(f"{n} ({e}d)" for _, n, e, _c in plan[:5])
    return f"Orchestrator plan: all 17 regions covered; top priorities: {top}"
