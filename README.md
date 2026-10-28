# jobhunt

Scrapes Hong Kong job boards, ranks every posting against a search profile, and stores them
in a local SQLite database so each run shows which jobs are **new** and you can track
where you've applied.

The profile targets junior-level roles in this order of preference: **product management**
(especially AI and insurtech products), then **business analyst**, then other good fits (AI
enablement, data, actuarial and financial reporting, project roles). It also filters out HK
construction and engineering "project manager" roles, which would otherwise flood the results.

## Sources

| Source   | How                                             |
|----------|-------------------------------------------------|
| JobsDB   | The JSON search endpoint the JobsDB site calls  |
| LinkedIn | The public (logged-out) job search; no login    |

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
