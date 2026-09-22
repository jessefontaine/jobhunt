# Changelog

Newest first. Every PR bumps `version` in `pyproject.toml` and adds a `## x.y.z — YYYY-MM-DD`
entry here; the browser UI shows this file on the dashboard and the new entries in its update
banner.

## 0.4.0 — 2026-09-22
- Regenerating preferences never rewrites the Manual rules you wrote yourself, or any other
  section you added; Claude is shown them as fixed context it may not restate or contradict.
- The rules it writes are now split in two: Learned for general patterns, Specifics for narrow
  one-off inferences about a single employer, method or caveat.
- Every action on the dashboard says when to use it, in a line under the button.

## 0.3.4 — 2026-09-21
- Dashboard: the What's new changelog is collapsed by default; click the heading to open it.

## 0.3.3 — 2026-09-21
- Public release: MIT license; the install command no longer needs `--from`
  (`uvx git+https://github.com/jessefontaine/jobhunt init DIR`).

## 0.3.2 — 2026-09-21
- CLAUDE.md files: the workspace one lists the layout (who writes each file) and the rules for
  the user's Claude; the engine one gets a module map and the test conventions.

## 0.3.1 — 2026-09-21
- README: a Setup section that walks through installing uv, Claude Code and git.

## 0.3.0 — 2026-09-21
- Dashboard: "Report a bug" and "Suggest a feature" links open a prefilled issue form on
  GitHub. The repo has matching issue templates.

## 0.2.0 — 2026-09-20
- The UI shows a banner when a newer engine is on GitHub and can update and restart itself.
- The dashboard shows this changelog.

## 0.1.0 — 2026-09-20
- Browser UI: dashboard with action buttons, rating queue, shortlist, rated page, and editors
  for the profile, preferences, CV and sources.yaml.
- Pipeline: fetch Dutch research listings, score them with Claude, write digests, learn
  preferences from ratings.
