"""Mohit's Autonomous Job Apply Portal - backend.

Run:  pip install -r requirements.txt && python app.py   ->  http://localhost:8000
Pulls jobs from open APIs, filters to your profile, ranks them, prepares a
tailored application for each, and (optionally) opens the real application
form in a browser, fills it in and waits for you to press Submit.
"""
import json, os, re, sqlite3, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_from_directory

BASE = Path(__file__).parent
PROFILE = json.loads((BASE / "profile.json").read_text())
COMPANIES = json.loads((BASE / "companies.json").read_text())
DB = BASE / "portal.db"
HEADERS = {"User-Agent": "Mozilla/5.0 (personal job portal)"}
CACHE = {"t": 0, "jobs": []}
CACHE_SECONDS = 30 * 60
LOCK = threading.Lock()
app = Flask(__name__, static_folder="static")


# ------------------------------------------------------------------ helpers
def clean(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t or "")).strip()


def get(url, **kw):
    r = requests.get(url, headers=HEADERS, timeout=10, **kw)
    r.raise_for_status()
    return r


def db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS apps(
        id TEXT PRIMARY KEY, title TEXT, company TEXT, url TEXT, source TEXT,
        status TEXT, ts REAL)""")
    return c


# ------------------------------------------------------------------ sources
def src_remotive():
    out = []
    for q in ("dotnet", "c#", "devops", "azure"):
        try:
            for j in get("https://remotive.com/api/remote-jobs", params={"search": q}).json()["jobs"]:
                out.append(dict(id=f"remotive-{j['id']}", title=j["title"], company=j["company_name"],
                    location=j.get("candidate_required_location", ""), remote=True, url=j["url"],
                    text=clean(j.get("description")), tags=j.get("tags", []),
                    salary=j.get("salary", ""), source="Remotive", posted=j.get("publication_date", "")))
        except Exception as e:
            print("remotive", q, e)
    return out


def src_remoteok():
    try:
        data = get("https://remoteok.com/api").json()[1:]
    except Exception as e:
        print("remoteok", e); return []
    return [dict(id=f"remoteok-{j['id']}", title=j.get("position", ""), company=j.get("company", ""),
        location=j.get("location", ""), remote=True, url=j.get("url", ""), text=clean(j.get("description")),
        tags=j.get("tags", []), salary=(f"${j['salary_min']:,}-${j['salary_max']:,}" if j.get("salary_min") else ""),
        source="RemoteOK", posted=j.get("date", "")) for j in data]


def src_arbeitnow():
    out = []
    for p in (1, 2, 3):
        try:
            for j in get("https://www.arbeitnow.com/api/job-board-api", params={"page": p}).json()["data"]:
                out.append(dict(id=f"arbeitnow-{j['slug']}", title=j["title"], company=j["company_name"],
                    location=j.get("location", ""), remote=bool(j.get("remote")), url=j["url"],
                    text=clean(j.get("description")), tags=j.get("tags", []), salary="",
                    source="Arbeitnow", posted=str(j.get("created_at", ""))))
        except Exception as e:
            print("arbeitnow", e); break
    return out


def src_himalayas():
    out = []
    for off in (0, 20, 40):
        try:
            for j in get("https://himalayas.app/jobs/api", params={"limit": 20, "offset": off}).json().get("jobs", []):
                locs = ", ".join(j.get("locationRestrictions") or []) or "Worldwide"
                sal = f"{j.get('currency','')} {j['minSalary']}-{j['maxSalary']}" if j.get("minSalary") else ""
                out.append(dict(id=f"himalayas-{j.get('guid') or j.get('applicationLink')}", title=j.get("title", ""),
                    company=j.get("companyName", ""), location=locs, remote=True,
                    url=j.get("applicationLink") or j.get("guid", ""), text=clean(j.get("description") or j.get("excerpt")),
                    tags=j.get("categories", []), salary=sal, source="Himalayas", posted=str(j.get("pubDate", ""))))
        except Exception as e:
            print("himalayas", e); break
    return out


def src_greenhouse():
    def one(tok):
        try:
            data = get(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs").json()["jobs"]
        except Exception:
            return []
        return [dict(id=f"gh-{tok}-{j['id']}", title=j["title"], company=tok.title(),
            location=(j.get("location") or {}).get("name", ""), remote=False, url=j["absolute_url"],
            text="", tags=[], salary="", source="Greenhouse", posted=j.get("updated_at", ""),
            ats="greenhouse") for j in data]
    with ThreadPoolExecutor(6) as ex:
        return [j for r in ex.map(one, COMPANIES.get("greenhouse", [])) for j in r]


def src_lever():
    def one(tok):
        try:
            data = get(f"https://api.lever.co/v0/postings/{tok}", params={"mode": "json"}).json()
        except Exception:
            return []
        return [dict(id=f"lv-{tok}-{j['id']}", title=j["text"], company=tok.title(),
            location=(j.get("categories") or {}).get("location", ""),
            remote="remote" in json.dumps(j.get("workplaceType", "")).lower(), url=j["hostedUrl"],
            text="", tags=[], salary="", source="Lever",
            posted=str(j.get("createdAt", "")), ats="lever") for j in data]
    with ThreadPoolExecutor(8) as ex:
        return [j for r in ex.map(one, COMPANIES.get("lever", [])) for j in r]


SOURCES = [src_remotive, src_remoteok, src_arbeitnow, src_himalayas, src_greenhouse, src_lever]


# ------------------------------------------------------------------ filter + rank
P = PROFILE["search"]


def classify(j):
    loc = (j["location"] or "").lower()
    blob = f"{loc} {j['text'][:500].lower()}"
    head = f"{j['title'].lower()} {loc} {j['text'][:800].lower()}"
    if any(r in head for r in P.get("region_locked", [])) and not any(g in head for g in P.get("global_words", [])):
        return None
    if j["source"] == "Arbeitnow" and not any(g in head for g in P.get("global_words", [])):
        return None
    if any(b in loc for b in P["remote_blocklist"]):
        return None
    if any(k in loc for k in P["onsite_cities"]):
        return "hybrid/onsite NCR" if not j["remote"] else "remote"
    if j["remote"] or "remote" in blob or "work from home" in blob:
        if not loc or any(k in loc for k in P["remote_ok"]):
            return "remote"
    return None


def rank(j):
    t, body = j["title"].lower(), f"{j['text'].lower()} {' '.join(map(str, j['tags'])).lower()}"
    if any(x in f"{t} " for x in P["exclude_title"]):
        return None
    if not any(m in f"{t} {body}" for m in P["must_match"]):
        return None
    where = classify(j)
    if not where:
        return None
    s = (5 if j["source"] in ("Greenhouse", "Lever") else 0) + sum(2 for h in P["title_hints"] if h in t) + sum(w for k, w in P["skills"].items() if k in body)
    if s < P["min_score"]:
        return None
    j["score"], j["where"] = s, where
    return j


STATE = {"jobs": [], "loading": False, "t": 0, "done": 0}


def _refresh():
    from concurrent.futures import as_completed
    STATE.update(loading=True, done=0)
    found, seen = {}, set()
    with ThreadPoolExecutor(len(SOURCES)) as ex:
        futs = [ex.submit(f) for f in SOURCES]
        for fut in as_completed(futs):
            try:
                raw = fut.result()
            except Exception as e:
                print("source failed", e); raw = []
            for j in raw:
                if j["id"] in seen:
                    continue
                seen.add(j["id"])
                if rank(j):
                    j["text"] = j["text"][:6000]
                    found[j["id"]] = j
            STATE["jobs"] = sorted(found.values(), key=lambda j: j["score"], reverse=True)
            STATE["done"] += 1
    STATE.update(loading=False, t=time.time())


def start_refresh(force=False):
    with LOCK:
        stale = time.time() - STATE["t"] > CACHE_SECONDS
        if STATE["loading"] or not (force or stale):
            return
        STATE["loading"] = True
    threading.Thread(target=_refresh, daemon=True).start()


def all_jobs(force=False):
    start_refresh(force)
    return STATE["jobs"]


# ------------------------------------------------------------------ tailoring
def facts():
    return "\n- ".join(PROFILE["facts"])


def tailor_template(job):
    hit = [k for k in P["skills"] if k in job["text"].lower()]
    skills = ", ".join(hit[:6]) or "C#, ASP.NET Core, SQL Server and Azure"
    pitch = (f"Hello {job['company']} team,\n\nI am applying for the {job['title']} role. I am a Software Engineer at "
             f"Coforge with 2+ years building enterprise backend systems in C# and ASP.NET Core, and your posting's focus on "
             f"{skills} matches my daily work. I run production deployments through TeamCity and Octopus Deploy, support "
             f"services on Azure App Service, and took part in a RabbitMQ to Azure Service Bus migration. I am now moving "
             f"deeper into DevOps (AZ-400 track).\n\nMy resume is attached. I would welcome a short call.\n\nMohit Varshney\n"
             f"{PROFILE['phone']} | {PROFILE['email']}")
    return dict(cover_note=pitch, matched=hit[:10], mode="template")


def tailor_ai(job):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return tailor_template(job)
    prompt = (f"Write a job application for Mohit. Use ONLY these facts; never invent anything.\nFACTS:\n- {facts()}\n\n"
              f"JOB: {job['title']} at {job['company']}\n{job['text'][:5000]}\n\n"
              'Return JSON only: {"match_percent":int,"matched":[str],"gaps":[str],"cover_note":"110-150 words, plain",'
              '"answers":{"why_this_role":str,"notice_period":"as stated in facts or say to confirm","strengths":str}}')
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=60,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": os.environ.get("TAILOR_MODEL", "claude-sonnet-4-5"), "max_tokens": 1200,
                  "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        txt = r.json()["content"][0]["text"]
        d = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        d["mode"] = "ai"
        return d
    except Exception as e:
        print("tailor_ai failed:", e)
        return tailor_template(job)


# ------------------------------------------------------------------ routes
@app.get("/")
def index():
    for d in (BASE / "static", BASE):
        if (d / "index.html").exists():
            return send_from_directory(str(d), "index.html")
    return "index.html is missing from the repo. Upload it next to app.py.", 500


@app.get("/api/jobs")
def api_jobs():
    jobs = all_jobs(force=request.args.get("refresh") == "1")
    done = {r[0] for r in db().execute("SELECT id FROM apps")}
    q = (request.args.get("q") or "").lower()
    where = request.args.get("where")
    out = [dict(j, applied=j["id"] in done) for j in jobs
           if (not q or q in f"{j['title']} {j['company']}".lower()) and (not where or j["where"] == where)]
    return jsonify(jobs=out[:200], total=len(jobs), fetched=int(STATE["t"]), loading=STATE["loading"], done=STATE["done"], sources=len(SOURCES))


@app.post("/api/prepare")
def api_prepare():
    jid = request.json["id"]
    job = next((j for j in all_jobs() if j["id"] == jid), None)
    if not job:
        return jsonify(error="Job not found. Refresh the list."), 404
    return jsonify(tailor_ai(job))


@app.post("/api/log")
def api_log():
    j = request.json
    c = db()
    c.execute("INSERT OR REPLACE INTO apps VALUES(?,?,?,?,?,?,?)",
              (j["id"], j["title"], j["company"], j["url"], j["source"], j.get("status", "Applied"), time.time()))
    c.commit()
    return jsonify(ok=True)


@app.get("/api/apps")
def api_apps():
    rows = db().execute("SELECT id,title,company,url,source,status,ts FROM apps ORDER BY ts DESC").fetchall()
    return jsonify([dict(zip("id title company url source status ts".split(), r)) for r in rows])


@app.post("/api/assist")
def api_assist():
    """Open the real form in a visible browser on THIS machine, fill it, wait for you."""
    jid = request.json["id"]
    job = next((j for j in all_jobs() if j["id"] == jid), None)
    if not job:
        return jsonify(error="Job not found."), 404
    note = (request.json.get("cover_note") or tailor_template(job)["cover_note"])
    try:
        import assist
    except Exception as e:
        return jsonify(error="Form filling works only in the laptop version of the portal, because it opens a browser window on your own screen."), 501
    threading.Thread(target=assist.run, args=(job, PROFILE, note, str(BASE / "resume.pdf")), daemon=True).start()
    return jsonify(ok=True, message="A browser window is opening. Review the filled form and press Submit yourself.")


start_refresh()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
