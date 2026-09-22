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

7. Existing tests

Run the broadest practical test set supported by the local environment. At minimum record:

- exact pytest command,
- test count/pass/fail,
- frontend build result,
- any tests skipped due to external auth/provider/runtime fixtures.

Do not convert an environment limitation into a fake PASS.

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
