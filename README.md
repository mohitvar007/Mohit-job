# Mohit's Autonomous Job Apply Portal

## Run on your laptop (works fully, including form filling)
    pip install -r requirements.txt
    pip install playwright && playwright install chromium
    python app.py          # open http://localhost:8000

## Put it online with a direct link (free, Render.com)
1. Upload this folder to a private GitHub repo.
2. Render.com > New > Blueprint > pick the repo (uses render.yaml).
3. Optional: add env var ANTHROPIC_API_KEY for cover notes written per job.
Your link will be https://mohits-job-portal.onrender.com (or similar).
The hosted version lists and prepares jobs. "Fill the form for me" needs the
laptop version, because it opens a browser window on your own screen.

## Tune
profile.json: skills, exclusions, cities. companies.json: add Greenhouse/Lever
company slugs (the part of their careers URL after boards.greenhouse.io/ or jobs.lever.co/).
