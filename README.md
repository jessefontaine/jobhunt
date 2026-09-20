# jobhunt

Fetch research / PhD job listings from Dutch sources, score them against *your* profile with
Claude, write a ranked markdown digest, and learn from the ratings you give so the next digest
is sharper. Python does everything deterministic; the `claude` CLI on your own subscription
does the judgement calls.

This repo is the **engine**. Your profile, CV, ratings and digests live in a private
**workspace** directory that `jobhunt init` creates for you.

## Quickstart

```bash
uvx --from git+https://github.com/jessefontaine/jobhunt jobhunt init ~/jobhunt
cd ~/jobhunt
# 1. edit profile/profile.md (what you want, what you can do, what rules a role out)
# 2. put your CV PDF in docs/ and run scripts/extract-cv.sh  (or write docs/cv.md by hand)
# 3. edit config/sources.yaml (queries; optional contact: for the User-Agent)
claude login                 # once — scoring runs through `claude -p`
uv run jobhunt check         # fetch → score → digests/<today>.md
```

Open the digest, fill in `rating:` (1–5) and optionally `note:` under listings, then
`uv run jobhunt rate`. Repeat. Rated listings never reappear; unrated ones do until you rate them.
After ≥3 new ratings, `rate` regenerates the `## Learned` rules in `profile/preferences.md`
(`--force` to do it sooner); the next `score` run reads them plus your rated listings as examples.
That only affects listings scored from then on — after editing your profile or preferences, run
`uv run jobhunt check --rescore` (or `score --rescore`) to re-score everything still open.

| rating | meaning |
|--------|---------|
| 5 | apply |
| 4 | strong interest |
| 3 | maybe |
| 2 | not really |
| 1 | irrelevant |

Inside Claude Code, open the workspace folder and type `/jobhunt`: it runs the same commands,
shows the top matches, and records ratings you give in chat.

If you see `OAuth session expired` / `preferences: failed`, run `claude login` in a terminal.

## Commands

| command | does |
|---|---|
| `jobhunt init DIR [--engine URL]` | create a workspace |
| `jobhunt check [--source X] [--no-score] [--rescore]` | fetch → score → digest |
| `jobhunt fetch [--source X]` | only fetch new listings into `data/jobs.sqlite` |
| `jobhunt score [--dry-run] [--rescore]` | score unscored listings (`--rescore`: every unexpired listing, replacing old scores; `--dry-run` prints the first prompt) |
| `jobhunt digest` | re-render a digest from the store |
| `jobhunt rate [FILE] [--force] [--no-learn] [--rebuild]` | ingest ratings from the newest (or given) digest |
| `jobhunt sources` | list sources and whether they are enabled |

All commands except `init` must run inside a workspace (a directory containing
`config/sources.yaml`) or be given `--root DIR`.

## Sources

| source | status |
|--------|--------|
| `academictransfer` | Fully automated. 10 newest per query, sorted by publish date, detail pages fetched. |
| `euraxess` | Automated, but the site currently ignores its own keyword/country filters (Sept 2026), so results are post-filtered to the configured country and usually empty. Rate-limits (429) quickly. |
| `pages` | Selector-driven scraper for institute pages (the template config has NIN and TNO keyword searches). Sites with no list markup are `manual: true` links. |
| `linkedin` | Public guest search, last 30 days, detail pages fetched. Falls back to a manual link if LinkedIn shows its sign-in wall. Noisy — the scorer sorts it out. |
| `indeed` | Manual only: Indeed blocks scrapers with a CAPTCHA, so the digest just links the saved searches. |

Fetching is polite (1 request/s, retries with backoff), so a full `check` takes several minutes
the first time and ~1–2 minutes afterwards (only new listings get detail pages and scores).
Blocked sites are never worked around; you get a link to open by hand.

## Workspace layout

| path | purpose |
|------|---------|
| `profile/profile.md` | who you are and what you want — hand-edited, read by the scorer |
| `profile/preferences.md` | `## Manual` rules (yours) + `## Learned` rules (regenerated from ratings) |
| `docs/cv.md` | plain-text CV, read by the scorer (`scripts/extract-cv.sh` makes it from a PDF) |
| `config/sources.yaml` | `contact`, `scoring` (model, batch size, examples), `digest.limit`, per-source settings |
| `data/ratings.jsonl` | append-only rating log — the durable record |
| `data/jobs.sqlite` | all listings ever seen + scores (gitignored, rebuildable) |
| `digests/` | one ranked markdown file per run — where rating happens |
| `pyproject.toml` | depends on this engine; `uv run jobhunt …` works from here; `uv sync --upgrade-package jobhunt` pulls a newer engine |

## Adding a source

1. Create `src/jobhunt/sources/<name>.py` with a class exposing `name` and
   `fetch(cfg, http) -> SourceResult`. Keep parsing in pure functions (`parse_search(html)`)
   and save a real page under `tests/fixtures/<name>/` to test against.
2. Register it in `src/jobhunt/sources/__init__.py`.
3. Add a block under `sources:` in your workspace's `config/sources.yaml` with `enabled: true`
   (and in `src/jobhunt/templates/config/sources.yaml.tmpl` if it should ship by default).

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

To run your own workspace against a local checkout, add to the workspace `pyproject.toml`:

```toml
[tool.uv.sources]
jobhunt = { path = "../path/to/this/checkout", editable = true }
```

Design notes: `docs/superpowers/specs/`.
