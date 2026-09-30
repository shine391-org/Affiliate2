# Douyin direct URL exact-ID resolution QA

## Scope and base

Branch: `fix/douyin-exact-aweme-resolution`.
Base: `0dc13b98a333d2ba196e5d9e77aa16b164625bff`.
No commit/push/merge performed. No second crawler, browser runtime, downloader,
new runtime dependency, DB schema change, or architecture change.

## Root cause and flow

The old resolver collected DOM media and `video/*` responses, but never queried
an exact aweme detail. With an empty manual-URL title, caption search did not
run; the error nevertheless claimed `dom_then_network_then_search`.

Live old-code reproduction for requested `7670961858774591397` failed with
observed IDs `7679399389807481977` and `7689856555759438419` from network media.
These were not the requested video. A separate live probe saw the page's native
detail response return the requested aweme. The existing provider's
`context.request` transport returned HTTP 403 for detail, but `fetch` within the
same authenticated Edge CDP page returned HTTP 200 with the correct detail.
Browser health alone did not establish player-media identity.

New order: DOM exact -> network exact -> exact-ID detail via existing provider
and temporary CDP page -> caption search only with a nonblank title -> fail closed.
API payload ownership is kept separately from URL/DOM evidence. Conflicting
payload IDs or media-URL IDs are rejected. URLs declaring multiple conflicting
IDs are rejected. Every alternate rendition passes the same gate.

Errors derive their resolution path from attempted stages, preserve observed
candidate IDs, and distinguish detail unavailable, provider exception types,
and no verified media. The downloader no longer blames a healthy cookie store
for an identity failure. Its outer timeout allows the additional bounded stage.

## Automated verification

Use the project venv from `E:/Affiliate2/app`.

- New initial regressions: 11 failed against old code (missing exact lookup and
  incorrect stage telemetry), then green after implementation.
- Search telemetry regressions: 2 failed before their fix, then green.
- Conflicting multi-ID URLs: 2 failed before tightening the gate, then green.
- Cookie-store diagnostic regression: failed before error-text fix, then green.
- Final focused scope: **156 passed**. Includes all `test_douyin*.py`, browser
  cookie/session tests, yt-dlp download tests, and task runner tests.
- Full suite: **3134 passed, 2 skipped**, 458.43 seconds. The final two multi-ID
  regressions/tightened guard were verified by the subsequent focused scope;
  this full run started before that final small change.
- Frontend `npm test`: **108 passed**; `npm run build`: PASS (existing chunk-size
  warning only).
- Backend `python -m compileall -q backend`: PASS. `git diff --check`: PASS.

Full-suite command:

```bash
PYTHONPATH='E:/Affiliate2;E:/Affiliate2/app' ../.venv/Scripts/python.exe -m pytest tests/ -q --tb=short
```

Focused command initializes an isolated real test DB because standalone
`test_ytdlp_download.py` otherwise lacks the `user_settings` schema:

```bash
../.venv/Scripts/python.exe -c "import runpy,asyncio,pytest,glob; runpy.run_path('tests/conftest.py'); from backend.database import init_db,engine; engine.echo=False; asyncio.run(init_db()); raise SystemExit(pytest.main(sorted(glob.glob('tests/test_douyin*.py'))+['tests/test_download_browser_session.py','tests/test_ytdlp_download.py','tests/test_task_runner.py','-q','--tb=short']))"
```

Initial full-suite attempt timed out at the tool boundary. A diagnostic rerun
stopped at `ModuleNotFoundError: src` (1861 passed); adding the repo root to
PYTHONPATH fixed that invocation error. Isolated tests without schema setup
failed with `no such table: user_settings`; no product code was changed to hide
these test-environment problems.

## Live verification (not mocked)

Used the authenticated existing Edge CDP at port 9222; did not touch Radar 9223.
Called production `download_video(url, output_dir=..., filename=..., extract_audio=True,
title_hint='')`, including settings/cookie probe, resolver, yt-dlp, and media/audio
contract. Probed the actual output containers with ffprobe.

| Requested = resolved aweme | Resolution | Creator UID | Actual output |
|---|---|---|---|
| 7670961858774591397 | exact_aweme | 57333005400 | H.264 1920x1080 + AAC, 70.423991s, 24,336,762 bytes |
| 7678658333595976822 | exact_aweme | 62817398032 | HEVC 1440x2560 + AAC, 279.542993s, 40,639,395 bytes |
| 7690423839238718922 | dom | 50497443174 | H.264 576x1024 + AAC, 11.333333s, 1,513,699 bytes |

Each extracted MP3 also exists and is nonempty. Each MP4's `vid:` comment matches
the `video_id` in the corresponding exact detail's play URL. The reported case
also passed a complete video+audio decode (`ffmpeg -v error ... -f null -`).

Live short URL `https://v.douyin.com/iRNBho6G/` froze requested ID
`3638929914889127993`, returned no available detail, and failed closed:
`resolution_path=dom_then_network_then_exact_aweme`,
`stage_outcomes=['exact_aweme=detail_unavailable']`. No title/search was fabricated.
This verifies the short-link failure path, not a successful short-link download.

Local evidence/probe directory:
`C:/Users/shine/AppData/Local/hermes/profiles/affiliate/cache/scratch/`

- `douyin_detail_probe.py`
- `douyin_download_repro.py`
- `douyin_live_matrix.py` and `douyin-live-matrix.json`
- `douyin-exact-live/<aweme_id>/evidence.json` and `<aweme_id>.mp4`
- `douyin-focused-final.log`, `douyin-full-regression-final.log`

## Review and known limits

Independent reviewer could not start (provider HTTP 404); there is no independent
review verdict. Adversarial self-review and executable regressions were used
instead, including payload/URL conflicts, rejected search results, blank titles,
access failure, timeout, stage ordering, page cleanup, and same-ID alternates.

Existing out-of-scope metadata issue: the top-level downloader `duration` is 0
for these direct CDN downloads, although ffprobe/media metadata has the actual
nonzero duration. The media bytes and audio contract pass; this task does not
change that metadata behavior. No live server restart/deployment is claimed.
