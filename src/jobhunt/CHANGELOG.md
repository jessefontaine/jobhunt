# Changelog

Newest first. Every PR bumps `version` in `pyproject.toml` and adds a `## x.y.z — YYYY-MM-DD`
entry here; the browser UI shows this file on the dashboard and the new entries in its update
banner.

## 0.2.0 — 2026-09-20
- The UI shows a banner when a newer engine is on GitHub and can update and restart itself.
- The dashboard shows this changelog.

## 0.1.0 — 2026-09-20
- Browser UI: dashboard with action buttons, rating queue, shortlist, rated page, and editors
  for the profile, preferences, CV and sources.yaml.
- Pipeline: fetch Dutch research listings, score them with Claude, write digests, learn
  preferences from ratings.
