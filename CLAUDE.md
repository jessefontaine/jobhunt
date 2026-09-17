# jobhunt — notes for Claude

Personal job-hunt pipeline. Read
[README.md](README.md) for the workflow and
[docs/superpowers/specs/2026-09-17-jobhunt-design.md](docs/superpowers/specs/2026-09-17-jobhunt-design.md)
for the design and the decisions behind it.

- `/jobhunt` runs the pipeline and records chat ratings — use it rather than scoring listings yourself.
- Scoring and preference learning go through the shell `claude` CLI (`claude -p`); if it reports
  `OAuth session expired`, the user has to run `claude login`.
- Sources are one file each in `src/jobhunt/sources/`; parsers are pure functions tested against
  saved HTML in `tests/fixtures/`. Re-save a fixture when a site changes markup.
- Tests: `uv run pytest`; lint: `uv run ruff check src tests`. TDD: test first.
- Never work around scraper blocks (403/CAPTCHA/sign-in walls) — surface a manual link instead.
- This repo holds personal data (CV); keep it private if it is ever pushed.
