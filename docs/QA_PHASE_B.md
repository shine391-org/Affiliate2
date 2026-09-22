# QA — Phase B Sources + Radar

This QA contract is intentionally focused. Do not run the full ~2450-test suite for every Phase B iteration.

## Required automated checks

For each provider/Radar commit:

- directly affected provider tests
- Radar normalization/ingest/ranking tests
- Scout/API smoke tests affected by the change
- frontend build only when Scout UI changes

Record exact commands and counts.

## Required behavior checks

### Platform identity

If user requests Bilibili:

- returned records are Bilibili records
- canonical URLs/IDs are Bilibili
- no YouTube fallback is returned as if it were Bilibili

If user requests Douyin:

- result is explicitly Douyin
- auth-required is surfaced as auth-required
- failed access is not silently replaced with another platform

### Radar observations

For repeated observations of the same content:

- content row remains platform-scoped
- snapshots append rather than overwrite
- velocity is calculated only when enough observations exist

### Creator baseline

Verify creator baseline uses that creator's own historical content within the same platform.

Do not compare creator identity across platforms.

### Runtime architecture

Fail QA if the new canonical provider path requires:

- launching MediaCrawler `main.py` as a subprocess
- reading MediaCrawler JSONL as the integration contract
- running MediaCrawler WebUI
- a second job system
- a second frontend

## Runtime smoke

When a provider milestone is integrated into Scout:

- run dev mode
- perform one real provider query where auth/environment permits
- verify returned platform identity and result normalization
- verify the app remains usable if provider reports auth-required/error

External login/network limitations are reported as environment constraints, not fake PASS.

## Exit gate

Phase B is ready when:

- app-native Bilibili provider works
- app-native Douyin provider path works in the local authenticated environment
- legacy subprocess/JSONL integration is no longer the canonical path
- Radar observations/ranking run from canonical app flow
- Scout exposes the integrated results
- focused QA passes


## Combined final Phase-B gate

Run B1–B5 as one QA pass after implementation is complete.

Required focused automated tests should include:

- `app/tests/test_source_providers.py`
- `app/tests/test_douyin_source_provider.py`
- `app/tests/test_affiliate_radar.py`
- `app/tests/test_scout_radar_enrichment.py`
- existing Scout regressions directly affected by provider dispatch

Also require:

- frontend production build
- one real Bilibili keyword query if network permits
- one real Douyin keyword query through the local authenticated CDP browser
- repeated observation check proving snapshot append + velocity availability after sufficient interval/data
- Scout API/UI smoke showing Radar fields for native-provider rows

Do not run the whole project suite unless a focused failure suggests a cross-cutting regression.
