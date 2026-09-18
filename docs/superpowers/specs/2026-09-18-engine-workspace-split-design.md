# Engine / workspace split — design spec

## Context

`jobhunt` started as one private repo holding both the pipeline code and the author's personal data
(CV, profile, digests, ratings). The goal now is to publish the pipeline on GitHub so that someone
else — same country (Netherlands), similar field (neuro / cognitive science), different profile —
can run it with their own profile, while the author keeps using it day to day.

Decisions already made (do not re-litigate):
- **Option B, engine + workspace.** The public repo contains only code ("the engine"). Each user
  has a private *workspace* directory holding their profile, config, CV, ratings, digests and
  database, plus a tiny `pyproject.toml` that depends on the engine. The workspace contains no code.
- The source set stays as it is (NL-centric). Generalising geography is out of scope.
- Author's layout after the split: engine at `~/Documents/code/jobhunt` (this repo, moved), private
  workspace at `~/Documents/jobhunt-jesse`.
- Public repo: `github.com/jessefontaine/jobhunt`, public. Engine history is kept but rewritten to
  drop personal paths *before* the first push.

## Workspace

A workspace is any directory containing `config/sources.yaml`. Full layout as created by
`jobhunt init DIR`:

```
DIR/
  pyproject.toml                 # name "jobhunt-workspace"; depends on jobhunt @ git+<engine url>
  README.md                      # 10-line workflow reminder, links to the engine README
  CLAUDE.md                      # workspace notes for Claude Code (use /jobhunt, claude login, …)
  .claude/skills/jobhunt/SKILL.md
  .gitignore                     # .venv/, data/jobs.sqlite*, __pycache__/
  config/sources.yaml            # today's config, with `contact:` and `indeed.domain:` added
  profile/profile.md             # template: sections with TODOs, incl. new `## Level`
  profile/preferences.md         # `## Manual` / `## Learned` skeleton
  docs/.gitkeep                  # user drops their CV PDF here
  scripts/extract-cv.sh          # pdftotext → docs/cv.md (unchanged script)
  data/.gitkeep
  digests/.gitkeep
```

`uv run jobhunt <cmd>` works from inside DIR because uv treats a `pyproject.toml` without a
build-system as a virtual project and installs its dependencies into `DIR/.venv`. Nothing else in
the engine assumes a package layout in the workspace.

Developers (including the author) point the workspace at a local checkout instead of the git URL
by adding to the workspace `pyproject.toml`:

```toml
[tool.uv.sources]
jobhunt = { path = "../code/jobhunt", editable = true }
```

## Engine changes

### Root discovery (`config.py`, `cli.py`)
- `find_root(start)` walks up from `start` (default cwd) to the first directory containing
  `config/sources.yaml`. Returns `None` if there is none (today it falls back to `start`).
- The CLI callback: `--root` if given, else `find_root()`; if neither yields a workspace, exit 1
  with `Not inside a jobhunt workspace (no config/sources.yaml found). Run: jobhunt init DIR`.
  `jobhunt init` itself must not require a workspace, so the callback skips the check for it
  (`ctx.invoked_subcommand == "init"`).
- `--root` help text updated accordingly. `Paths` is unchanged.

### Config keys (`config.py`)
- Top-level `contact: <string>` (optional). Exposed as `Config.contact: str | None`.
- `indeed.domain` (optional, default `www.indeed.com`); read inside `IndeedSource.fetch` like the
  other per-source keys. `search_url(query, location, domain)` builds
  `https://<domain>/jobs?q=…&l=…`.

### HTTP client (`sources/http.py`)
- `PoliteClient(contact: str | None = None, …)`. User-Agent is
  `jobhunt/0.1 (personal job search; contact: <contact>)` when set, else
  `jobhunt/0.1 (personal job search)`. The module-level `USER_AGENT` constant with the author's
  email is removed; `cli._fetch` passes `ctx.config.contact`.

### Prompts (`scoring.py`, `ratings.py`)
- `INSTRUCTIONS` opens with *"You are helping the person described in the profile below triage
  job listings."* The MSc/postdoc sentence is replaced by: *"Respect the career level and
  constraints stated in the profile: roles requiring qualifications this person does not have
  should score low unless the listing is explicitly open to their level."*
- `PREFERENCES_INSTRUCTIONS` opens with *"Below are job listings the person described in the
  profile has rated …"*. (The preferences prompt does not currently include the profile; that is
  unchanged — the sentence just stops asserting who they are.)
- Nothing else in the prompts changes; the JSON contract and schemas are untouched.

### `jobhunt init DIR [--engine URL]` (`cli.py`, new `scaffold.py`)
- Templates live in `src/jobhunt/templates/` mirroring the workspace tree. Every template file
  carries a `.tmpl` suffix (so a nested `.gitignore`, `pyproject.toml` or `CLAUDE.md` cannot
  affect the engine repo itself); `scaffold` strips it on write. Read through
  `importlib.resources.files("jobhunt.templates")` so they ship in the wheel — verified once by
  `uv build` + listing the wheel, since editable installs would not catch a packaging gap.
- `--engine` default: `https://github.com/jessefontaine/jobhunt`. It is substituted into
  `pyproject.toml.tmpl` (`{engine_url}`); no other template has placeholders.
- Behaviour: create DIR if missing; if DIR exists and is non-empty, exit 1 with a message (no
  partial writes). Copy every template, make `scripts/extract-cv.sh` executable, then print the
  next steps:
  1. edit `profile/profile.md` (and `profile/preferences.md` → `## Manual`),
  2. put your CV PDF in `docs/` and run `scripts/extract-cv.sh` (or write `docs/cv.md` by hand),
  3. `claude login` once,
  4. `uv run jobhunt check`, then rate in the digest / via `/jobhunt`.
- `init` does not run `git init`; the README tells the user to.

### Templates content
- `config/sources.yaml`: current file plus `contact: TODO your@email` (commented example) at the
  top and `domain: nl.indeed.com` under `indeed`. The header comment says which lines to edit.
- `profile/profile.md`: same section headings as the author's profile — **Targets (ranked)**,
  **Research interests**, **Methods & experience**, **Skills**, **Background**, **Level** (new:
  "current degree / stage, and what that rules in or out"), **Constraints**, **Dealbreakers**,
  **Strong positives** — each with a one-line hint and a `TODO` bullet. No CV-derived content.
- `profile/preferences.md`: the current skeleton (`## Manual` with "(none yet)", `## Learned`).
- `SKILL.md`, `extract-cv.sh`, `.gitkeep`s: moved verbatim from the engine repo.
- `CLAUDE.md` (workspace): "/jobhunt runs the pipeline; scoring uses `claude -p`, run
  `claude login` if it reports OAuth expired; this directory holds personal data, keep it private".
- `README.md` (workspace): workflow (`check` → rate → `rate`), rating scale, pointer to the
  engine README for sources and options.

### Engine repo housekeeping
- Removed from the engine: `.claude/skills/jobhunt/`, `scripts/`, `profile/`, `docs/cv.md`,
  `docs/Academic CV.pdf`, `data/`, `digests/` (all now templates or personal).
- `README.md` rewritten: what it does; **Quickstart** (`uvx --from git+https://github.com/jessefontaine/jobhunt jobhunt init ~/jobhunt`, fill profile, `cd`, `uv run jobhunt check`); workflow and rating
  scale; sources table (unchanged); config reference (`contact`, `scoring`, `digest`, per-source
  keys); adding a source; development (`uv sync`, `uv run pytest`, `uv run ruff check`, the
  `[tool.uv.sources]` editable trick).
- `CLAUDE.md` (engine) rewritten: no personal references; keeps the rules that still apply
  (TDD, `uv run pytest`, parsers as pure functions with fixtures, never work around scraper
  blocks). Notes that there is no workspace here, so `jobhunt check` won't run from the engine dir.
- `pyproject.toml`: description and authors unchanged (author attribution is fine in public);
  `pypdf` dropped from the dev group (unused).
- The 2026-09-17 design spec is dropped from history and re-added with the CV-derived details
  (background, GPA, supervisors, "Verified facts" paragraph) removed; the design sections stay.

## Author migration (one-off, run in this order)

1. Implement and commit the engine changes above in the current repo (still at
   `~/Documents/jobhunt`).
2. `uv run jobhunt init ~/Documents/jobhunt-jesse`, then copy in the real `profile/profile.md`
   (adding a `## Level` section: MSc student; postdoc/PhD-required roles only if explicitly open to
   MSc graduates), `profile/preferences.md`, `docs/Academic CV.pdf`, `docs/cv.md`,
   `digests/2026-09-18.md`, `data/ratings.jsonl`, `data/jobs.sqlite`. Add `contact:` to
   `config/sources.yaml`. Switch `pyproject.toml` to the editable local source. `git init`, commit.
   Verify: `uv run jobhunt digest` reproduces the digest from the copied database.
3. `mv ~/Documents/jobhunt ~/Documents/code/jobhunt`.
4. In the engine: `uvx git-filter-repo --invert-paths` for `docs/Academic CV.pdf`, `docs/cv.md`,
   `profile/`, `digests/2026-09-18.md`, `data/ratings.jsonl`,
   `docs/superpowers/specs/2026-09-17-jobhunt-design.md`. Commit the redacted spec.
5. Verify: `git log --all --stat` contains none of those paths; `git grep` over all commits for
   the phone number and for `Academic CV` finds nothing; `uv run pytest` and `ruff` pass.
6. User runs `gh auth login`; then, after explicit confirmation, `gh repo create
   jessefontaine/jobhunt --public --source . --push`. Point the workspace's `[tool.uv.sources]`
   comment at the now-real URL (the editable path stays).

## Testing

- `test_config.py`: `find_root` finds the nearest `config/sources.yaml`, returns `None` outside a
  workspace; `contact` parsed (present/absent).
- `test_cli.py`: any command outside a workspace exits 1 with the message; `init` creates the
  full tree, `pyproject.toml` contains the engine URL, refuses a non-empty dir, leaves an existing
  empty dir usable.
- `test_http.py`: User-Agent with and without `contact`.
- `test_indeed.py`: default domain `www.indeed.com`; `domain: nl.indeed.com` honoured.
- `test_scoring.py` / `test_preferences.py`: prompt text contains "person described in the
  profile" and the level sentence, and does not contain "Master's student" or "MSc".
- New `test_scaffold.py`: every expected template path exists in the package and is non-empty.
- All existing tests keep passing; tmp-root fixtures already write `config/sources.yaml`.

## Error handling

- Outside a workspace: clear exit-1 message (above). No stack trace.
- `init` into a non-empty dir: exit 1 before writing anything.
- Missing `contact`: no error; anonymous User-Agent.
- Everything else is unchanged from the original design.
