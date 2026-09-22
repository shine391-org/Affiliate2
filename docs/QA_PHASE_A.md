# QA — Consolidation Phase A

QA this branch as a behavior-preserving application-baseline migration.

Branch:
`consolidation-phase-a-app-baseline`

Base:
`workspace-sync-2026-09`

## Hard constraints

Phase A must not intentionally change Scout, downloader, Translate/Dub, Creative Remake, Review state transitions, Publish behavior or Performance logic.

`vendor/viralmint/` and `vendor/mediacrawler/` must remain unchanged.

## Required checks

1. Source parity

```powershell
git diff --no-index -- vendor/viralmint app
```

Expected immediately after the Phase A promotion commit: no source differences.

If later Phase A fixes are required in `app/`, document them explicitly instead of editing `vendor/viralmint/`.

2. Bootstrap

```powershell
.\scripts\bootstrap.ps1
```

Verify Python environment and frontend dependencies install without changing tracked vendor/reference code.

3. Frontend build

```powershell
Push-Location app\frontend
npm run build
Pop-Location
```

4. Backend import/startup

```powershell
.\scripts\dev.ps1 -StableBackend
```

Verify `http://localhost:5173` loads and API requests reach port 16888.

5. Core pages

Open and smoke-check:

- Scout
- Library
- Tools
- Clip Studio
- Review Queue
- Performance
- Settings

Review Queue and Performance are especially important because they prove the existing Affiliate2 overlay is still loaded from the promoted app.

6. Stable mode

```powershell
.\scripts\stop.ps1
.\scripts\run.ps1
```

Verify built SPA loads at `http://localhost:16888`.

7. Focused tests — REQUIRED

Phase A is a source-parity / launcher baseline change. Do NOT run the ~2450-test full suite as a blocking check for every iteration.

Required automated checks:

```powershell
pytest tests/test_api_tools_router.py tests/test_api_smoke.py
```

Also run any additional tests directly affected by a Phase A fix.

Required non-pytest checks:

- frontend production build,
- backend startup/import,
- dev UI smoke,
- stable-mode UI smoke,
- source parity.

Full test suite — OPTIONAL / milestone only:

Run the complete suite only when:
- preparing the final consolidation merge/release,
- shared core/database behavior changed,
- focused tests expose a cross-cutting regression,
- or CI/background execution is convenient.

A long full-suite run is not a Phase A blocker when no broad runtime behavior was changed.

Always record exactly what was run. Do not convert untested behavior into PASS.

## Failure conditions

QA FAIL if any of these occur:

- app source is incomplete compared with the captured ViralMint baseline,
- bootstrap or launch scripts write secrets to Git,
- Review Queue/Performance routes disappear,
- frontend cannot call backend in dev mode,
- stable mode serves stale/missing frontend,
- launch scripts kill unrelated processes,
- Phase A changes product behavior without an explicit documented reason.

## QA report format

```
STATUS: PASS | PARTIAL | FAIL

BRANCH / HEAD:

SOURCE PARITY:

BOOTSTRAP:

FRONTEND BUILD:

BACKEND START:

DEV UI:
- Scout:
- Library:
- Tools:
- Clip Studio:
- Review Queue:
- Performance:
- Settings:

STABLE MODE:

TESTS ACTUALLY RUN:

REGRESSIONS:

BLOCKERS:

RECOMMENDATION:
READY FOR PHASE B | FIX REQUIRED
```
