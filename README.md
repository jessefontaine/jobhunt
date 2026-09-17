# jobhunt

Personal pipeline for finding PhD / research / neuro-cogpsy roles in the Netherlands, ranking
them against my profile, and learning from my ratings.

Design spec: [docs/superpowers/specs/2026-09-17-jobhunt-design.md](docs/superpowers/specs/2026-09-17-jobhunt-design.md)

## Workflow

```bash
uv run jobhunt check          # fetch new listings → score with Claude → write digests/<today>.md
```

Or, inside Claude Code in this folder, type `/jobhunt` — it runs the same command, shows the top
matches in chat, and records the ratings you give conversationally.

Scoring uses the `claude` CLI on your subscription (`claude -p`), so the CLI must be logged in:
if you see `OAuth session expired` / `preferences: failed`, run `claude login` once in a terminal.

Open the newest digest, fill in `rating:` (1–5) and optionally `note:` under each listing:

| rating | meaning |
|--------|---------|
| 5 | apply |
| 4 | strong interest |
| 3 | maybe |
| 2 | not really |
| 1 | irrelevant |

```bash
uv run jobhunt rate           # ingest ratings from the newest digest, update profile/preferences.md
```

Repeat. Rated listings never reappear; unrated ones do until you rate them. `jobhunt rate`
regenerates the `## Learned` rules in `profile/preferences.md` once you have given ≥3 new ratings
(`--force` to do it anyway); the next `score` run reads them plus your rated listings as examples.

Other commands: `jobhunt fetch [--source X]`, `jobhunt score [--dry-run]`, `jobhunt digest`,
`jobhunt sources`, `jobhunt rate --rebuild` (replay `data/ratings.jsonl` into a fresh database).

## Sources

| source | status |
|--------|--------|
| `academictransfer` | Fully automated. 10 newest per query, sorted by publish date, detail pages fetched. |
| `euraxess` | Automated, but the site currently ignores its own keyword/country filters (Sept 2026), so results are post-filtered to Netherlands and usually empty. Rate-limits (429) quickly. |
| `pages` | Selector-driven scraper for institute pages: NIN and TNO keyword searches. Spinoza, Amsterdam UMC, Donders are `manual: true` links (no list markup / JS-only). |
| `linkedin` | Public guest search, last 30 days, detail pages fetched. Falls back to a manual link if LinkedIn shows its sign-in wall. Noisy — the scorer sorts it out. |
| `indeed` | Manual only: Indeed blocks scrapers with a CAPTCHA, so the digest just links the saved searches. |

Fetching is polite (1 request/s, retries with backoff), so a full `check` takes several minutes
the first time and ~1–2 minutes afterwards (only new listings get detail pages and scores).

## Scheduling (later)

Once the pipeline is trustworthy, a systemd user timer or cron entry running
`cd ~/Documents/jobhunt && uv run jobhunt check` daily is all that's needed; the digest lands in
`digests/` for you to rate whenever.

## What's where

| path | purpose |
|------|---------|
| `profile/profile.md` | who I am and what I want — hand-edited, read by the scorer |
| `profile/preferences.md` | `## Manual` rules (yours) + `## Learned` rules (regenerated from ratings) |
| `docs/Academic CV.pdf`, `docs/cv.md` | CV; `scripts/extract-cv.sh` regenerates the text version |
| `config/sources.yaml` | which sources are enabled, their queries; scoring model |
| `data/ratings.jsonl` | append-only rating log — the durable record (committed) |
| `data/jobs.sqlite` | all listings ever seen + scores (gitignored, rebuildable) |
| `digests/` | one ranked markdown file per run — where rating happens |
| `src/jobhunt/sources/` | one file per job site |

## Adding a source

1. Create `src/jobhunt/sources/<name>.py` with a class exposing `name` and
   `fetch(cfg, http) -> SourceResult`. Keep parsing in pure functions (`parse_search(html)`)
   and save a real page under `tests/fixtures/<name>/` to test against.
2. Register it in `src/jobhunt/sources/__init__.py`.
3. Add a block under `sources:` in `config/sources.yaml` with `enabled: true`.

## Development

```bash
uv sync
uv run pytest
uv run ruff check
```

This repo contains personal data (CV with phone number). If it is ever pushed, keep it private.
