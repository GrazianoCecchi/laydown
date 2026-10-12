# Laydown state audit — 2026-10-12

Auditor: opencode agent (task AUDIT-1). Scope: read/run/report; nothing was fixed.
Working tree: branch `agent/opencode`, clean, at commit 2080224 ("Site: unglue the flat
sections, use the real logo, refresh the screenshot"). Evidence is file:line, a command
plus its output, or a URL, current as of 2026-10-12.

## 1. Gate results

- `QT_QPA_PLATFORM=offscreen $HOME/.venvs/laydown/bin/python -m pytest -q`
  → **258 passed, 1 skipped** in ~5s. Matches the lead's known numbers exactly.
- Skipped test: `tests/test_determinism.py:105` — reason reported by pytest:
  "no German locale installed (tried de_DE.UTF-8, de_DE.utf8, de_DE, German_Germany.1252,
  de-DE)". This is the ROADMAP Phase-1 *culture-invariance* invariant (comma decimals
  corrupting PDF number syntax, ROADMAP.md:64-66); it cannot run in this WSL image.
  Whether CI's runners have the locale and therefore exercise it is UNVERIFIED from here.
- `QT_QPA_PLATFORM=offscreen $HOME/.venvs/laydown/bin/python -m laydown --smoke`
  → exit 0, all four steps "ok" (sample build, 2-up A3 impose, verification, offscreen
  main window), final line `SMOKE OK — Laydown 0.4.5`.

## 2. Version consistency

Single source of truth: `laydown/__init__.py:6` → `__version__ = "0.4.5"`.

| Location | Value | Matches 0.4.5? |
|---|---|---|
| `laydown/__init__.py:6` | 0.4.5 | source of truth |
| `pyproject.toml:7,36-38` | dynamic `{attr = "laydown.__version__"}` | yes (derived) |
| `Laydown.spec:20,94-97` | imports `__version__` (exe/CFBundle versions) | yes (derived) |
| `AppxManifest.xml:11` | literal `Version="0.3.0.0"` | no — but stamped at build time from the installed package's `laydown.__version__` (`packaging/windows/build.ps1:40-43,115-117`); the tracked literal is a stale placeholder, cosmetic only |
| `README.md` | no literal version; `<version>` placeholders (e.g. README.md:35) | yes |
| `HANDOFF.md:7,16,19` | 0.4.5 | yes |
| `docs/ai/START_HERE.md:6,9` | **0.4.4** | **NO — drift** |
| `CHANGELOG.md:7` | [0.4.5] — 2026-07-19 | yes |
| `site/src/` | no version literals; download link is `releases/latest` (`site/src/pages/index.astro:22`) | yes |
| `docs/ai/GOTCHAS.md:92`, `docs/ai/DECISIONS.md:42` | 0.4.5 / 0.4.0 mentions | historical, correct |

The ROADMAP backlog item "single-source the version (it's in six places today)"
(ROADMAP.md:163) is **no longer true**: only one literal remains (the AppxManifest
placeholder, which the build rewrites); every other consumer derives from `__init__.py`.

## 3. Docs drift

- `docs/ai/START_HERE.md:6` — "Current version **0.4.4**" → false; 0.4.5
  (`laydown/__init__.py:6`, latest release v0.4.5).
- `docs/ai/START_HERE.md:9` — "GitHub releases (0.4.4, all platforms)" → false; latest
  is v0.4.5 (`gh release list`: v0.4.5 Latest, 2026-07-18).
- `docs/ai/START_HERE.md:32` — "243 of them" (tests) → false; 258 passed, 1 skipped.
- `docs/ai/START_HERE.md:10` — "Microsoft Store submission (in review since 2026-07-18)"
  → UNVERIFIED; ~3 months have passed with no recorded outcome (see §4).
- `HANDOFF.md:21` — "Tests | 257" → false; 258 passed, 1 skipped.
- `HANDOFF.md:133` — "pytest # 257 tests" → false; same.
- `README.md:134` — "pytest # 235 tests" → false; same.
- `HANDOFF.md:20,54-58` — Store "IN REVIEW" since 2026-07-18, package 0.4.1 → cannot be
  confirmed or refuted from outside Partner Center; no public listing is findable (§4).
  UNVERIFIED, and the "what to do when the review comes back" plan (HANDOFF.md:76-84) is
  unactioned after ~3 months — at minimum the status line needs a re-check by the owner.
- `ROADMAP.md:21` — "0.4.1 released (2026-07-18)" vs `CHANGELOG.md:59` "0.4.1 —
  2026-07-17" — one-day inconsistency, minor.
- AGENTS.md: no stale version or test-count claims found (grep for version/test counts:
  no hits).
- Positive: HANDOFF's "four build jobs" (HANDOFF.md:42) is accurate in substance —
  release.yml has windows/linux/macos jobs plus a gated `release` job; macOS arm64 and
  Intel come from one matrixed job (`runs-on: ${{ matrix.runner }}`,
  `.github/workflows/release.yml:137-141`), i.e. four platform builds.

## 4. Distribution state

- **Releases** (`gh release list -R danielmevit/laydown`): 8 tags, v0.2.0 (2026-02-19)
  through **v0.4.5 (Latest, 2026-07-18T22:26Z)**. Matches HANDOFF.md:19.
- **CI** (`gh run list -R danielmevit/laydown -L 15`): last run 2026-07-18T23:57Z
  ("Deploy Laydown to Pages", main, success). Of the 15 listed: 14 success, 1 failure —
  a `Release` run on the v0.4.1 tag push (id 29635925405, 2026-07-18T07:32Z), which was
  followed ~1h later by the successful v0.4.1 release run (29637694381). No runs since
  2026-07-18; no commits since 2026-07-19.
- **Pages**: https://danielmevit.github.io/laydown/ → HTTP 200; /privacy/ → HTTP 200
  (curl, 2026-10-12). Live.
- **Microsoft Store**: cannot confirm any public listing. `apps.microsoft.com` search
  pages are JS-rendered (curl yields no greppable results); the edge store-search API
  returned a service-unavailable page; a web search for "Laydown" on apps.microsoft.com
  returned zero results. So the 0.4.1 submission's outcome (still in review / rejected /
  published under another name) is **UNVERIFIED** and needs a Partner Center login.
- **Branch divergence**: `git fetch` + `git rev-list --left-right --count
  origin/main...origin/dev` → **0 0**. Both branches point at the same commit 2080224
  (2026-07-19 01:57:42 +0200). Confirms "no commits since 2026-07-19" and that no work
  is stranded on dev.

## 5. Dependencies

Declared (`pyproject.toml:16-19`): `PyQt6>=6.4.0`, `PyMuPDF>=1.23.0`; dev extras
(`pyproject.toml:22-25`): `pytest>=7.0.0`, `pytest-qt>=4.2.0`. Floors only — everything
floats at install time.

Installed in the owner venv (`pip list --format=freeze`, read-only):

| Package | Installed | OSV query result (api.osv.dev, 2026-10-12) |
|---|---|---|
| PyQt6 | 6.11.0 | `{}` — no advisories |
| PyQt6-Qt6 | 6.11.1 | `{}` — no advisories |
| PyQt6_sip | 13.11.1 | not queried (generated binding shim) |
| PyMuPDF | 1.28.0 | `{}` — no advisories |
| pytest | 9.1.1 | `{}` — no advisories |
| pytest-qt | 4.5.0 | not queried (dev-only, not shipped) |

CI pins nothing: `pip install -e ".[dev]" pyinstaller` in all build jobs
(`.github/workflows/release.yml:49,114,164`; `.github/workflows/store.yml:47`). PyInstaller
is not even declared in pyproject — it floats entirely. Risk: a breaking PyInstaller or
PyQt6 release breaks all four platform builds at once, three months stale today.

## 6. Repo hygiene (ROADMAP.md:163-165 backlog, checked against this revision)

- "version in six places" — **resolved** (see §2): one source, one stale-but-stamped
  placeholder (`AppxManifest.xml:11`). The ROADMAP item is stale itself.
- "extract `pressready-voice/`" — **done**: directory absent; not in `git ls-files`.
- "delete the untracked `_legacy/`" — **done**: absent.
- "decide `framer-demo/`'s home" — **still open**: `framer-demo/` remains tracked at the
  repo root (`git ls-files`: index.html, package.json, package-lock.json, src/App.jsx,
  src/index.css, src/main.jsx, vite.config.js).
- Tracked junk: none — `git ls-files | grep -E '__pycache__|\.pyc$|egg-info'` → 0.
  `__pycache__/` dirs exist locally but are gitignored.

## 7. Code health quick scan

TODO/FIXME/HACK markers: **none** in `laydown/`, `tests/`, `site/`, `packaging/` (grep
over .py/.js/.astro; zero hits).

Observed defects/risks (≤10, each with evidence and confidence):

1. `laydown/ui/main_window.py:398-401` — preflight is wrapped in
   `except Exception: self._findings = []`. Any bug *inside preflight* is silently shown
   to the user as "no findings" — indistinguishable from a clean job. The comment
   ("never let a check break the app it is checking") justifies the intent, but the
   exception is neither logged nor surfaced. Confidence: high (code read).
2. `AppxManifest.xml:11` — stale literal `Version="0.3.0.0"`; readers mistake it for the
   real version (it is rewritten at build, `build.ps1:115`). A `0.0.0.0` placeholder or
   comment would prevent that. Confidence: high.
3. Docs disagree with each other on the test count in four places (235/243/257 vs the
   real 258+1) — see §3. This is exactly the drift the audit exists to catch before it
   misleads the next session. Confidence: high.
4. CI build toolchain unpinned (`.github/workflows/release.yml:49,114,164`;
   `store.yml:47`) — PyInstaller floats with no floor and is undeclared in pyproject;
   PyQt6/PyMuPDF install at whatever is latest on release day. Reproducibility and
   release stability depend on upstream goodwill. Confidence: high.
5. The culture-invariance test skips locally (`tests/test_determinism.py:105`), so the
   invariant ROADMAP calls central to Phase 1 runs only where a German locale exists
   (presumably CI runners — UNVERIFIED). If CI runners ever drop the locale, the guard
   silently stops executing anywhere. Confidence: medium.
6. `laydown/engine/impose.py:110-120` + `laydown/ui/preview_panel.py:156-161` — temp
   PDF lifecycle checked: cleaned up on all paths I read (caller `finally` unlink).
   No defect found; noted as verified-clean. Confidence: high.
7. No 32-bit Windows build (HANDOFF.md:50, GOTCHAS) — still true and still documented;
   not a defect, recorded so nobody re-opens it. Confidence: high.
8. `ROADMAP.md:163-165` backlog lists three hygiene items as open that are already done
   (§6) — stale plan makes the next agent redo settled work. Confidence: high.

## 8. Recommended next tasks

**Agent can do:**
1. **Docs sync pass** (S) — fix START_HERE 0.4.4→0.4.5 + test counts, HANDOFF 257→258,
   README 235→258. Why now: four files disagree with the code today. Files:
   docs/ai/START_HERE.md, HANDOFF.md, README.md. Risk: none.
2. **ROADMAP hygiene-item cleanup** (S) — mark version-six-places, pressready-voice and
   _legacy as done; keep only the framer-demo decision. Why now: the plan lies about the
   repo. Files: ROADMAP.md. Risk: none.
3. **AppxManifest placeholder hygiene** (S) — set the literal to `0.0.0.0` or add a
   comment pointing at `build.ps1` stamping. Why now: prevents the exact confusion in
   §7.2. Files: AppxManifest.xml. Risk: low — verify the stamp regex still matches.
4. **Surface preflight self-failures** (S) — log or show a note when the preflight pass
   itself throws, instead of `self._findings = []` silently. Why now: silence is the
   app's defined worst failure mode. Files: laydown/ui/main_window.py:398-401. Risk: low.
5. **Pin the release toolchain** (M) — constraints file (PyQt6, PyMuPDF, PyInstaller)
   used by CI, floors kept in pyproject. Why now: releases are 3 months stale; first
   release after a long gap is exactly when unpinned deps bite. Files:
   .github/workflows/release.yml, store.yml, new constraints file. Risk: medium —
   needs a validation tag before the next real release.
6. **Headless CLI** (M) — ROADMAP backlog; the engine is already Qt-free (AGENTS.md).
   Why now: it's the highest-value agent-buildable feature with zero UI risk.
   Files: laydown/engine/, new laydown/cli.py, tests. Risk: low (engine is tested).

**Owner manual step:**
7. **Check Partner Center for the Store submission** (S) — 0.4.1 has been "in review"
   per HANDOFF.md:20 for ~3 months with no recorded outcome; no public listing is
   findable (§4). Then update HANDOFF §3 and, if approved, submit the 0.4.5 catch-up
   package per HANDOFF.md:81-84/86-99. Why now: the plan's trigger event is either
   overdue or already fired unnoticed. Risk: none to check; the resubmission path is
   already documented.
8. **Decide framer-demo/'s home** (S) — move to its own repo or delete from this one;
   it's the last open hygiene item (§6). Agent can execute once the owner decides.

---

SUMMARY: gates green and matching the lead's numbers (258 passed + 1 skipped for locale,
smoke OK at 0.4.5); repo frozen since 2026-07-19 with main == dev at 2080224; docs drifted
in four places (START_HERE still on 0.4.4, three different wrong test counts); Store
submission outcome UNVERIFIED and ~3 months overdue for a check; dependencies clean per
OSV but unpinned in CI; three of four ROADMAP hygiene items already done; code is clean
of TODO markers with one real finding (silent preflight failure) plus a stale AppxManifest
version literal.
FILES CHANGED: docs/ai/audits/2026-10-12-state-audit.md (only).
TESTS RUN + RESULTS: pytest -q → 258 passed, 1 skipped (test_determinism.py:105, no
German locale); laydown --smoke → SMOKE OK, exit 0.
ASSUMPTIONS: the owner venv versions equal what CI last built with (CI installs floats,
so exact CI build versions are UNVERIFIED); OSV empty result = no known advisories.
RISKS: Store status may have changed since any page was cached; version floors mean a
future CI run may pick up newer deps than audited here.
NEEDS-REVIEW: Store submission status (owner, Partner Center); whether CI runners exercise
the German-locale determinism test; decision on framer-demo/.
