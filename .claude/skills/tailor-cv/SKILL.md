---
name: tailor-cv
description: Tailor the user's CV to a job on the HK Job Desk, following their existing CV as a strict template and never adding facts. Use for "tailor my CV for <job>", and from the morning run for each new pick without a tailored CV.
---

# Tailor a CV

Dashboard: https://claude.ai/artifact/DvVMwRvqcSDEpBjgEiJRXT

## Hard rules (the user's own guardrails)

1. **Never fabricate.** Use only facts in the base CVs (`cvs/*` in the dashboard database) and
   in `about/me` (the user's About me page, which also records facts they confirmed in chat).
   No new numbers, tools, employers, titles, dates, outcomes, scope or seniority. Rewording is
   fine; upgrading a claim is not ("supported" must not become "led"). If a job needs
   something the sources don't support, leave it out and say so in the change log.
   If you are unsure whether a fact is supported, ask the user instead of using it.
2. **The existing CV is the template.** Same header, section order, formatting, sentence
   structure and bullet style. Pick the base by role: `PM` for product roles, `BA` for business
   analyst roles, `Data-Based` for AI/data roles.
3. **One A4 page.** The base CVs already fill the page, so a tailored CV may never have more
   words or more bullets than its base. `python -m jobhunt cv-check` enforces this and flags any
   number or capitalised term not found in the sources.
4. **User decisions already made:** add the FWD role (Assistant Product Manager, Jun 2026 –
   Present, GenAI Actuarial team) as the latest work entry, worded only from `about/me`; drop the
   Leadership section to make room. Respect anything newer in `about/me` over this list.

## Steps

1. Read the job (`jobs/<id>`): title, company, description. Note the 3–5 requirements that
   matter most.
2. Load sources into a scratch folder `$W/sources/`: `ArtifactData` `list` `cvs` with `out_dir`
   `$W/sources`, and `get` `about/me` (save it as `$W/sources/about/me.json`). Copy the chosen
   base to `$W/base.json`.
3. Write `$W/tailored.json` in the CV JSON shape (see `jobhunt/cv.py`):
   - Summary: refocus on the job's top requirements using sourced facts; keep the base's length
     or shorter.
   - Insert the FWD entry first under WORK EXPERIENCE; choose the FWD bullets that best match the
     job (Financial Reporting Wiki, workbook variable catalogue, AI Builders module).
   - Drop the Leadership section, then remove the least relevant older bullet(s) until the bullet
     count is at or below the base's.
   - Optionally reorder the Technical skills line to lead with what the job asks for, and swap a
     bullet for another CV version's wording of the *same* facts.
   - Mark every changed piece of text as `{"text": ..., "change": n}` and list each change in
     `changes`: `where`, `before`, `after`, `why` (tie it to the job's requirement), `sources`.
   - Add `job_id`, `job_title`, `company`, `base`, `generated_at` (ISO time).
4. Check: `python -m jobhunt cv-check $W/tailored.json --base $W/base.json --sources $W/sources`.
   It writes `checks` into the file. Fix and re-run until `ok` is true. Never hand-edit `checks`.
   An unsourced term means rewrite it out, not add it to the sources.
5. PDF: `python -m jobhunt cv-pdf $W/tailored.json --out $W/pdf`. It prints the PDF and records
   `pdf.pages`; it must be exactly 1 (if not, trim and go back to step 4). Upload it:
   `Artifact` publish with the dashboard `url`, `asset: true`, `file_path` = the PDF. Put the
   returned id in the document as `pdf: {"filename", "pages", "asset_id"}` and drop `pdf.path`.
6. Save: `ArtifactData` `set` collection `tailored`, `doc_id` = the job id, `file_path`
   `$W/tailored.json` (pin `if_version` if a tailored doc already exists).
7. Tell the user in a line or two what you emphasised, and anything the job wants that their
   sources don't show.
