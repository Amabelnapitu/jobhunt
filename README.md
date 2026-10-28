# jobhunt

Scrapes Hong Kong job boards, ranks every posting against a search profile, and stores them
in a local SQLite database so each run shows which jobs are **new** and you can track
where you've applied.

The profile targets junior-level roles in this order of preference: **product management**
(especially AI and insurtech products), then **business analyst**, then other good fits (AI
enablement, data, actuarial and financial reporting, project roles). It also filters out HK
construction and engineering "project manager" roles, which would otherwise flood the results.

## Daily workflow

1. **Every morning** a Claude scheduled task follows `.claude/skills/morning-run`: it scrapes,
   reads each promising job's page, follows LinkedIn leads to the employer's site and JobsDB,
   ranks everything, and picks today's top 20 jobs not yet applied to.
2. **The dashboard** ([HK Job Desk](https://claude.ai/artifact/DvVMwRvqcSDEpBjgEiJRXT), private)
   shows the picks, why each ranks where it does, and what each application needs: documents,
   salary/notice details, cover letter. Fill those in and press *Ready to apply*.
3. **Tailored CVs**: each pick gets a CV tailored from your existing CV versions
   (`.claude/skills/tailor-cv`). The dashboard shows it on an A4 page beside a numbered list of
   every change, why it was made, and where the facts come from. Rules: no fact that isn't in
   your CVs or your About me page; same template; never more words or bullets than the base CV
   (it fills the page). `python -m jobhunt cv-check` enforces the budget and flags any number or
   name not found in your sources.
4. **Saved & earlier, tracker**: jobs you save, and picks from earlier days, stay available after
   they drop out of the top 20. The tracker shows every application by stage, with dates.
5. **Applying**: in the Claude app, say "apply to my ready jobs". Claude follows
   `.claude/skills/apply-jobs`: fills each form in your signed-in browser, shows you the filled
   form, and submits only after your OK. It never invents answers and never applies twice.

### Ranking

Each job gets:

- **Eligibility** from `[candidate]` in `profile.toml`: `ineligible` if it requires Cantonese,
  written Chinese, fluent Mandarin, HK permanent residency, or 3+ years' experience; `check` for
  2 years (a stretch) or unclear language asks; otherwise `eligible`.
- **Interview odds** (0-100): experience fit, junior/senior title, how many profile keywords
  match, LinkedIn applicant count, rare languages the posting wants (Korean, Bahasa), Chinese
  languages only "preferred", recruitment agency, insurance/actuarial domain.
- **Freshness** (0-100): newest postings first.
- **Rank** = 40% fit + 35% interview odds + 25% freshness (weights in `[ranking]`).
  Ineligible jobs aren't ranked; `check` jobs are discounted 15%.

Each job also records its **application link**, how you apply (JobsDB, employer site, LinkedIn
Easy Apply) and the **documents and details the posting asks for**.

### Dashboard database

| Collection     | Written by           | Holds |
|----------------|----------------------|-------|
| `jobs/<id>`    | morning run, once    | job details, description, apply link, documents and info needed |
| `runs/<stamp>` | morning run          | that run's picks, and every job's rank, eligibility and scores |
| `tracking/<id>`| you, apply workflow  | your answers, documents ticked, cover letter, status, applied date |
| `profile/me`   | you                  | standing answers (expected salary, availability, ...) |
| `about/me`     | you                  | your experience and achievements, in your words: a tailoring source |
| `cvs/<variant>`| Claude, from Drive   | your CV versions in structured form (the tailoring templates) |
| `tailored/<id>`| morning run / Claude | the tailored CV for a job, its change log, and check results |

CVs and personal details live only in the dashboard's private database, never in this repo.

The page source is in `dashboard/index.html`.

## Sources

| Source   | How                                             |
|----------|-------------------------------------------------|
| JobsDB   | The JSON search endpoint the JobsDB site calls, plus each job's page |
| LinkedIn | The public (logged-out) job search and job pages; no login |
| Employer careers sites | Followed from LinkedIn's apply links: Greenhouse and Lever public APIs, or the page's schema.org JobPosting data |

LinkedIn jobs with a short description or Easy Apply only are also looked up on JobsDB (same
title and company), which often has the full text and a way to apply without LinkedIn. When a
site rate-limits, the scraper stops asking it for the rest of the run; it never works around
blocks.

## Run it

Needs Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m jobhunt
```

Every job found is saved to `data/jobs.db`. Each run also writes `output/jobs.csv` (opens in
Excel), sorted best match first, and prints the top 25 with `NEW` marking jobs not seen before.

```bash
python -m jobhunt scrape --sources jobsdb    # one source only
python -m jobhunt scrape --days 3            # just the last few days
python -m jobhunt list --status new --top 30 # browse stored jobs (shows ids)
python -m jobhunt list --category product    # product roles only
python -m jobhunt mark 42 applied --notes "referral from X"
python -m jobhunt export --min-score 15      # CSV of strong matches only
```

`python -m jobhunt` with no command means `scrape`. Jobs scoring below 0 (wrong field, too
senior) stay in the database but are left out of `list` and the CSV unless you pass a lower
`--min-score`.

## Database

SQLite: a single file with no server to run. Open it in [DB Browser for SQLite](https://sqlitebrowser.org/)
or query it from Python, Power BI or Excel (ODBC).

| Table         | One row per                              | Notes |
|---------------|------------------------------------------|-------|
| `jobs`        | posting (deduped on title + company)     | scraped fields, `score`, `category`, `first_seen`, `last_seen`, plus your `status` and `notes` |
| `runs`        | scrape run                               | when, which sources, jobs found, how many new |
| `job_queries` | (job, search query) pair                 | which queries surface each job |

`status` is one of `new`, `interested`, `applied`, `interviewing`, `offer`, `rejected`,
`ignored`. Re-scraping refreshes the scraped fields but never touches `status` or `notes`.

Example: strong matches you haven't acted on yet.

```sql
SELECT id, score, title, company, url FROM jobs
WHERE status = 'new' AND score >= 15 ORDER BY score DESC;
```

## Tuning

Edit `profile.toml`:

- `[search].queries`: what gets typed into each job board.
- `[scoring.keywords]`: points for each keyword. A keyword in the job title counts double.
- `[scoring.penalties]`: deductions for title words (seniority, construction, sales, ...).
- `[scoring.level_bonus]`: small boost for the seniority level you're targeting.
- `[categories]`: maps job titles to a role family (`product`, `business_analysis`, `ai_data`,
  `actuarial_finance`, `project`, else `other`). The first matching category wins.
- `[scoring.category_bonus]`: extra points per role family. This is where "product first,
  then BA" is set.

Each scrape re-scores every job already in the database, so profile changes apply to old results too.

## Notes

- Requests are spaced 1 to 2 s apart. Don't crank `max_pages` way up; job boards rate-limit.
- Job boards change their pages from time to time. If a source starts returning 0 results or
  `FAILED`, its parser in `jobhunt/sources/` needs updating.
- This repo is public: keep CVs and contact details out of it (`cv/`, `*.pdf`, `*.docx` and
  `data/` are git-ignored).

## Tests

```bash
pip install pytest
python -m pytest
```
