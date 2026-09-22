# Changelog

Newest first. Every PR bumps `version` in `pyproject.toml` and adds a `## x.y.z — YYYY-MM-DD`
entry here; the browser UI shows this file on the dashboard and the new entries in its update
banner.

## 0.5.0 — 2026-09-22
- Settings: a new `config/settings.yaml` and a Settings page in the UI. Light/dark/auto theme
  with a toggle in the header, a score range that the queue and new digests obey, queue order,
  a "closes soon" deadline marker, digest size, scoring model and batch size, and the shortlist
  threshold. Existing workspaces keep working: the scoring and digest values are read from
  sources.yaml until you save the page once.
- The navigation bar stays at the top of the page when you scroll.
- Rated listings are no longer re-scored. Check with "rescore everything open" used to spend a
  Claude call on listings you had already rated; it skips them now (`jobhunt score --rescore
  --include-rated` brings the old behaviour back).
- The Rated page hides ratings below 3 once they are older than 30 days, both adjustable in
  Settings. Nothing is deleted: they still teach your preferences, and `/rated?all=1` shows
  them again.
- Regenerating preferences now condenses instead of accumulating: rules are merged when they
  overlap, a specific that three ratings support is promoted to a general rule, and the caps
  (10 rules, 8 specifics, 20 words each) are yours to change in Settings.
- Add a link: paste a vacancy page on the dashboard, or run `jobhunt add <url>`, and it is
  fetched, stored and scored like any other listing. If the site blocks the fetch, add it by
  hand with --title, --employer and --description. `jobhunt show <url>` prints what the store
  knows about one listing.
- The dashboard says what the update check is doing: what it tracks, when it last ran, the git
  error if it failed, and why checks are off for a development checkout, a commit pin or an
  install that did not come from git. `jobhunt update --check` prints the same from a terminal,
  and `jobhunt update` applies it. New `jobhunt learn` regenerates preferences from the CLI.
- Your workspace's Claude can now talk about roles (it reads your profile, preferences and CV,
  and starts from the score the engine already gave), turn a link you paste into a stored
  listing, and correct a preference it inferred wrongly — the correction goes into your own
  Manual rules, which the engine never rewrites. A new /jobhunt-sources skill adds a site to
  watch without touching engine code.

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
