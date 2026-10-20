"""Assisted apply: open the job's real form in a visible browser, fill the
standard fields from profile.json, attach the resume, then WAIT for you to
review and press Submit. Works best on Greenhouse and Lever forms; on other
sites it fills whatever standard fields it can find.

Needs:  pip install playwright && playwright install chromium
"""
from playwright.sync_api import sync_playwright

FIELDS = [  # (label/name/placeholder regex, profile key)
    (r"first.?name", "first_name"), (r"last.?name", "last_name"),
    (r"^(full )?name$|your name", "full_name"), (r"e-?mail", "email"),
    (r"phone|mobile", "phone"), (r"linkedin", "linkedin"),
    (r"current (company|employer)|employer|organi[sz]ation", "current_company"),
    (r"location|city", "location"),
]


def run(job, profile, cover_note, resume_path):
    profile = dict(profile, full_name=f"{profile['first_name']} {profile['last_name']}")
    url = job["url"]
    if job.get("ats") == "lever" and not url.rstrip("/").endswith("/apply"):
        url = url.rstrip("/") + "/apply"
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        page = b.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        # Greenhouse pages put the form behind an Apply button on some boards.
        for sel in ("text=Apply for this job", "text=Apply now", "a:has-text('Apply')"):
            btn = page.locator(sel).first
            if btn.count() and not page.locator("input[type=file]").count():
                try:
                    btn.click(timeout=2000); page.wait_for_timeout(1500)
                except Exception:
                    pass
        import re
        for pat, key in FIELDS:
            rx = re.compile(pat, re.I)
            for loc in (page.get_by_label(rx), page.get_by_placeholder(rx)):
                try:
                    el = loc.first
                    if el.count() and el.is_visible() and not el.input_value():
                        el.fill(str(profile.get(key, ""))); break
                except Exception:
                    continue
        try:
            page.locator("input[type=file]").first.set_input_files(resume_path)
        except Exception:
            pass
        for sel in ("textarea[name*=cover i]", "textarea[aria-label*=cover i]", "textarea[name='comments']", "textarea"):
            try:
                t = page.locator(sel).first
                if t.count() and t.is_visible() and not t.input_value():
                    t.fill(cover_note); break
            except Exception:
                continue
        print("Form filled. Review it, answer any extra questions, solve any CAPTCHA, and press Submit yourself.")
        try:
            page.wait_for_event("close", timeout=30 * 60 * 1000)
        except Exception:
            pass
        b.close()
