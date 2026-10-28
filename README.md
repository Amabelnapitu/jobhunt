# jobhunt

Scrapes Hong Kong job boards, ranks every posting against a search profile, and keeps a
running CSV so each run shows which jobs are **new**.

The profile is tuned for project / product / business-analyst roles in insurance, actuarial,
and GenAI. It also filters out HK construction and engineering "project manager" roles,
which would otherwise flood the results.

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

Results go to `output/jobs.csv` (opens in Excel), sorted best match first. The top 25 are
printed to the terminal, with `NEW` marking jobs not seen in an earlier run.

Useful options:

```bash
python -m jobhunt --sources jobsdb        # one source only
python -m jobhunt --days 3                # just the last few days
python -m jobhunt --min-score 15          # keep only strong matches
```

By default, jobs that score below 0 (wrong field, too senior) are left out.

## Tuning

Edit `profile.toml`:

- `[search].queries`: what gets typed into each job board.
- `[scoring.keywords]`: points for each keyword. A keyword in the job title counts double.
- `[scoring.penalties]`: deductions for title words (seniority, construction, sales, ...).
- `[scoring.level_bonus]`: small boost for the seniority level you're targeting.

Re-running re-scores every job already in the CSV, so profile changes apply to old results too.

## Notes

- Requests are spaced 1 to 2 s apart. Don't crank `max_pages` way up; job boards rate-limit.
- Job boards change their pages from time to time. If a source starts returning 0 results or
  `FAILED`, its parser in `jobhunt/sources/` needs updating.
- This repo is public: keep CVs and contact details out of it (`cv/`, `*.pdf` and `*.docx` are
  git-ignored).

## Tests

```bash
pip install pytest
python -m pytest
```
