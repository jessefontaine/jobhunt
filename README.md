# jobhunt

Personal pipeline for finding PhD / research / neuro-cogpsy roles in the Netherlands, ranking
them against my profile, and learning from my ratings.

Design spec: [docs/superpowers/specs/2026-09-17-jobhunt-design.md](docs/superpowers/specs/2026-09-17-jobhunt-design.md)

## Workflow

```bash
uv run jobhunt check          # fetch new listings → score with Claude → write digests/<today>.md
```

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

Repeat. Rated listings never reappear; unrated ones do until you rate them.

Other commands: `jobhunt fetch`, `jobhunt score`, `jobhunt digest`, `jobhunt sources`,
`jobhunt rate --rebuild` (replay `data/ratings.jsonl` into a fresh database).

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
