# jobhunt

Fetch research / PhD job listings from Dutch sources, score them against *your* profile with
Claude, write a ranked markdown digest, and learn from the ratings you give so the next digest
is sharper. Python does everything deterministic; the `claude` CLI on your own subscription
does the judgement calls.

This repo is the **engine**. Your profile, CV, ratings and digests live in a private
**workspace** directory that `jobhunt init` creates for you.

## Setup

Linux or macOS (on Windows, use WSL). You need three tools on your PATH; Python itself is not
one of them — uv fetches 3.12+ when the workspace needs it.

1. **[uv](https://docs.astral.sh/uv/getting-started/installation/)** installs and runs the
   engine:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **[Claude Code](https://code.claude.com/docs/en/setup)** does the scoring and preference
   learning through `claude -p`, on your own Claude subscription — no API key:

   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   claude login
   ```

3. **git** — usually already there (`git --version`). uv installs the engine from GitHub with
   it, and the browser UI's update check runs `git ls-remote`.

Optional: `pdftotext` (poppler) if you want `scripts/extract-cv.sh` to make `docs/cv.md` from a
PDF; otherwise write `docs/cv.md` by hand.

## Quickstart

```bash
uvx git+https://github.com/jessefontaine/jobhunt init ~/jobhunt
cd ~/jobhunt
# 1. edit profile/profile.md (what you want, what you can do, what rules a role out)
# 2. put your CV PDF in docs/ and run scripts/extract-cv.sh  (or write docs/cv.md by hand)
# 3. edit config/sources.yaml (queries; optional contact: for the User-Agent)
#    config/settings.yaml has the rest: theme, score range, digest size, scoring model
uv run jobhunt check         # fetch → score → digests/<today>.md
uv run jobhunt serve         # or: the browser UI (buttons, rating, shortlist, editors)
```

Open the digest, fill in `rating:` (1–5) and optionally `note:` under listings, then
`uv run jobhunt rate`. Repeat. Rated listings leave the queue; unrated ones stay until you rate them.
Listings you rated 4–5 that are still open appear in a `## Shortlist` at the top of every digest
and in `shortlist.md` (`uv run jobhunt shortlist` prints it). To change a rating, edit the
`rating:` line in the digest you rated it in and run `uv run jobhunt rate digests/<that-file>.md`.
After ≥3 new ratings, `rate` regenerates `profile/preferences.md` (`--force` to do it sooner):
`## Learned` gets the general patterns, `## Specifics` the narrow one-off inferences, and your
own `## Manual` rules are left exactly as you wrote them — they are shown to Claude as rules it
may not contradict. The next `score` run reads all three plus your rated listings as examples.
That only affects listings scored from then on — after editing your profile or preferences, run
`uv run jobhunt check --rescore` (or `score --rescore`) to re-score everything still open.

To see whether the scores are worth trusting, `uv run jobhunt calibration` ranks Claude's
scores against your ratings (Spearman) and prints the mean rating per score band — which is
also how you find the right shortlist threshold. It needs ~10 ratings to mean anything.

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

## Web UI

`uv run jobhunt serve` opens http://127.0.0.1:8765 with the same pipeline behind buttons:

- **Dashboard** — counts, *Check / Fetch / Score / Digest* with option boxes for `--source`,
  `--no-score` and `--rescore`, *Add a link* (paste a vacancy page: it is fetched, stored and
  scored), *Regenerate preferences* (shows how many ratings arrived since the last time), and a
  live log of the running job. One job runs at a time.
- **What's new** — the engine's changelog, what the update check tracks, when it last ran and
  the git error if it failed, plus a *Check now* button. An amber banner appears on every page
  when GitHub has a newer engine: it lists the new entries and **Update & restart** runs
  `uv sync --upgrade-package jobhunt`, checks the new code imports, and restarts the server in
  place (the page reloads by itself). Development checkouts are not updated this way, and the
  panel says so rather than staying silent.
- **Settings** — `config/settings.yaml` with real controls: light/dark/auto theme, the score
  range the Queue and digests show, queue order, the "closes soon" window, digest size, scoring
  model and batch size, the shortlist threshold, when a low rating drops off the Rated page,
  the caps that keep learned preferences condensed, and the update check interval.
- **Queue** — open, unrated listings best-first; click 1–5 (and type a note) to rate. Ratings go
  straight into `data/ratings.jsonl` and the store, so digests are just a record here.
- **Shortlist** and **Rated** — what you rated 4–5 that is still open; everything you rated,
  where you change an old rating. Ratings below the threshold drop off the Rated page once they
  are older than the window (`/rated?all=1` shows them; nothing is ever deleted).
- **Profile / Preferences / CV / Sources** — edit the workspace files in place
  (`sources.yaml` is validated before saving).
- **Feedback** — *Report a bug* / *Suggest a feature* open a prefilled issue form on GitHub
  (engine version and OS already filled in); you write the rest and submit it there.

It binds to localhost without authentication; `--host 0.0.0.0` exposes it, including file
editing, to your network.

## Commands

| command | does |
|---|---|
| `jobhunt init DIR [--engine URL]` | create a workspace |
| `jobhunt check [--source X] [--no-score] [--rescore]` | fetch → score → digest |
| `jobhunt fetch [--source X]` | only fetch new listings into `data/jobs.sqlite` |
| `jobhunt score [--dry-run] [--rescore] [--include-rated]` | score unscored listings (`--rescore`: every unexpired, unrated listing, replacing old scores; `--include-rated` re-scores rated ones too; `--dry-run` prints the first prompt) |
| `jobhunt add URL [--title …] [--employer …] [--description …] [--no-score]` | store a listing from a link and score it; the options add one by hand when the site blocks the fetch |
| `jobhunt show URL-or-id` | print a listing with its score, why, concerns and your rating |
| `jobhunt digest` | re-render a digest from the store |
| `jobhunt shortlist` | print open listings rated 4–5 and write `shortlist.md` |
| `jobhunt calibration` | check the scores against your ratings: rank correlation and the mean rating per score band |
| `jobhunt rate [FILE] [--force] [--no-learn] [--rebuild]` | ingest ratings from the newest (or given) digest |
| `jobhunt sources` | list sources and whether they are enabled |
| `jobhunt learn` | regenerate the learned preferences from every rating (one Claude call) |
| `jobhunt update [--check]` | update the engine from GitHub, or report why it cannot be checked |
| `jobhunt serve [--host H] [--port N] [--no-open]` | run the browser UI |

All commands except `init` must run inside a workspace (a directory containing
`config/sources.yaml`) or be given `--root DIR`.

## Sources

| source | status |
|--------|--------|
| `academictransfer` | Fully automated. 10 newest per query, sorted by publish date, detail pages fetched. |
| `euraxess` | Automated, but the site currently ignores its own keyword/country filters (Sept 2026), so results are post-filtered to the configured country and usually empty. Rate-limits (429) quickly. |
| `pages` | Selector-driven scraper for institute pages (the template config has NIN and TNO keyword searches). This is how users add their own sites — the workspace's `/jobhunt-sources` skill writes the entry. Sites with no list markup are `manual: true` links. |
| `linkedin` | Public guest search, last 30 days, detail pages fetched. Falls back to a manual link if LinkedIn shows its sign-in wall. Noisy — the scorer sorts it out. |
| `indeed` | Manual only: Indeed blocks scrapers with a CAPTCHA, so the digest just links the saved searches. |
| `manual` | Not polled: one listing per link added with `jobhunt add` (or the dashboard's *Add a link*). |

Fetching is polite (1 request/s, retries with backoff), so a full `check` takes several minutes
the first time and ~1–2 minutes afterwards (only new listings get detail pages and scores).
Blocked sites are never worked around; you get a link to open by hand.

## Workspace layout

| path | purpose |
|------|---------|
| `profile/profile.md` | who you are and what you want — hand-edited, read by the scorer |
| `profile/preferences.md` | `## Manual` rules (yours, never touched) + `## Learned` and `## Specifics` (regenerated from ratings) |
| `docs/cv.md` | plain-text CV, read by the scorer (`scripts/extract-cv.sh` makes it from a PDF) |
| `config/sources.yaml` | where to look: `contact` and per-source settings (queries, `pages:` entries) |
| `config/settings.yaml` | how results are shown and how the pipeline behaves (theme, score range, digest size, scoring model, rated-list hiding, preference caps, update checks) |
| `data/ratings.jsonl` | append-only rating log — the durable record |
| `data/jobs.sqlite` | all listings ever seen + scores (gitignored, rebuildable) |
| `digests/` | one ranked markdown file per run — where rating happens |
| `shortlist.md` | open listings you rated 4–5, refreshed by `check`, `digest`, `rate` and `shortlist` |
| `pyproject.toml` | depends on this engine; `uv run jobhunt …` works from here; `uv sync --upgrade-package jobhunt` pulls a newer engine |

## Adding a source

Most sites need no engine code: add an entry under `pages:` in the workspace's
`config/sources.yaml` (the `/jobhunt-sources` skill in a workspace does this interactively —
it reads the site's markup, writes the selectors and verifies them). A site that needs an API,
pagination or a search form gets a module here:

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

Every PR bumps `version` in `pyproject.toml` and adds an entry at the top of
[`src/jobhunt/CHANGELOG.md`](src/jobhunt/CHANGELOG.md) — the UI shows it, and a test checks
the two agree.

To run your own workspace against a local checkout, add to the workspace `pyproject.toml`:

```toml
[tool.uv.sources]
jobhunt = { path = "../path/to/this/checkout", editable = true }
```

The browser UI lives in `src/jobhunt/web/` (FastAPI + Jinja2; `tests/test_web.py` drives it
with a test client). Its `templates/` are web pages — `src/jobhunt/templates/` are the
workspace scaffolding files, a different thing.

Design notes: `docs/superpowers/specs/`.

## License

[MIT](LICENSE).
