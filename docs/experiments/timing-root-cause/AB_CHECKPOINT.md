# A/B — Parent QA checkpoint

## Scope and verdict

**LOCAL VERIFIED timing replay + LIVE VERIFIED leaf Edge TTS artifacts.** This is NOT a fresh C6→C7→render→QC pipeline baseline and does not measure `auto_complete_video_rate`. The corpus uses recorded final C7 spoken text from 8 independent Chinese sources (including the 2 supplied legacy videos), 154 chunks. Fresh +0% audio is available for every chunk.

The old delegated batch failed through agent repetition. Its real audio was retained. Parent independently checked 714 original audio files with ffprobe, duration, text/voice identity and SHA256. Baseline hashes were compared with their recorded values; A/B hashes, which the old harness omitted from rows, were newly captured in `parent-audio-audit.json` (not represented as historic hash provenance).

## Isolated strategy results

All strategies use the same current production `allocate_silence` and bounded global retime, replayed per video from SOURCE coordinates. True hard overflow is required speed >1.35; soft is >1.30 through 1.35. Canonical `required_speed` is clamped at 1.0; the old child report's sub-1.0 percentiles were duration ratios, not canonical required speed.

| Strategy | Hard /154 | Soft /154 | Timing-feasible videos /8 | Newly feasible videos | P50 | P90 | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Fresh +0 baseline | 11 | 1 | 7 | 0 | 1.0000 | 1.2605 | 1.5316 |
| A +10% | 7 | 3 | 7 | 0 | 1.0000 | 1.1519 | 1.3471 |
| A +20% | 3 | 5 | 7 | 0 | 1.0000 | 1.1192 | 1.2980 |
| A +30% | 2 | 2 | 7 | 0 | 1.0000 | 1.1192 | 1.2166 |
| B punctuation candidates | 9 | 1 | 7 | 0 | 1.0000 | 1.2397 | 1.4150 |

A has 63 measured chunks at each of +10/+20/+30 (189 calls); unmeasured chunks reuse their fresh +0 baseline. All baseline hard chunks and original 5 regression cases have every rate arm; at least 2 fitting controls per video are present. These projections are experimental uniform rate interventions on the selected subset, NOT a production adaptive rate policy. Native rate and any subsequent post-fit speed are distinct; listening/naturalness is not verified.

B considered 25 candidates, synthesized 14 at +0%, skipped 11. Exact lexical tokens are preserved. This does NOT prove removed pauses preserve prosody or meaning. In particular vocatives, repeated acknowledgments and command boundaries need human auditory review. Do not promote the heuristic to blanket comma removal.

## Per-video fresh baseline

| Source | Chunks | Fit | Soft | Hard | Timing feasible |
|---|---:|---:|---:|---:|---|
| commons:75746142 | 12 | 12 | 0 | 0 | Yes |
| commons:75687222 | 10 | 10 | 0 | 0 | Yes |
| commons:72844427 | 16 | 16 | 0 | 0 | Yes |
| commons:164833398 | 10 | 10 | 0 | 0 | Yes |
| commons:164825067 | 10 | 10 | 0 | 0 | Yes |
| commons:107550546 | 13 | 13 | 0 | 0 | Yes |
| legacy:25babc2c-deaa-48b9-8bad-f9b417d39a6f | 70 | 58 | 1 | 11 | No |
| legacy:f1a3f70c-cf5e-4fab-b691-3ffbe5bd87ea | 13 | 13 | 0 | 0 | Yes |

All hard failures occur in ONE source. No language-wide or per-voice causal percentage is justified from this dataset. Source, voice, discourse and C7 rewrite history are confounded. Baseline is post-recorded-recovery text, not untouched C6 translation.

## Rescue details

All rescued IDs below belong to legacy25. Rescued means no longer HARD, not necessarily normal fit or approved audio.

- +10%: 43, 54, 60, 64 (4).
- +20%: 39, 43, 54, 59, 60, 62, 64, 69 (8).
- +30%: 39, 43, 54, 56, 59, 60, 62, 64, 69 (9).
- Punctuation: 43, 60 (2), provisional pending pause/meaning acceptance.
- No strategy makes the sole failing video timing-feasible. No new hard failures in these replay projections.
- Chunk43 exact text/voice +0 punctuation comparison: 2390→1585 ms after canonical trim; 805 ms saving. This is only a measured candidate, not blanket production acceptance.

## Outlier repeat investigation

Original A +30 had retained anomalies: legacy25 chunk40 `Được` 357→939 ms and commons72844427 chunk7 `bến phà` 725→938 ms. Re-running canonical trim on copies of original raw audio reproduces 939/938 ms, so it is not merely JSON arithmetic.

Eight fresh calls (2 cases ×2 repetitions ×2 rates) preserve originals and retain word-boundary events:

- `Được`: +0 357 ms; +30 307 ms in BOTH repeats (14.01% reduction).
- `bến phà`: +0 725 ms; +30 588 ms in BOTH repeats (18.90% reduction).

Original anomalies did NOT reproduce in fresh TTS, but their exact audio/prosody root cause remains unresolved. Do not erase them or promise a deterministic rate curve. Both controls already fit; the anomalies do not change the hard-rescue counts above.

## Focused verification

Executed:

```
<project-venv-python> -E -X utf8 ab_parent_audit.py
<project-venv-python> -E -X utf8 ab_repeat_anomalies.py
<project-venv-python> -E -X utf8 -m unittest test_ab_parent_audit test_diagnostic_corpus test_diagnostic_de -v
```

Results: audit exit0; all 8 repeat calls LIVE; 19 focused tests PASS. Tests cover exact baseline coverage, duplicate/error rejection, cold/partial-resume identity, all rate arms for hard+regressions, fitting controls for each source, exact replay of all 11 recorded manifest overflow values, canonical clamped speeds, and separate B lexical-vs-semantic status. No full suite run. No production redesign or policy changes.

## Remaining work

C is still running in its replacement delegation. D/E evidence lives in `../de/`; the sole structural grouping candidate was rejected for a strong discourse boundary. Full report and any first production-change recommendation remain NOT COMPLETE. Live end-to-end automation, auditory naturalness, source fidelity and independent per-voice attribution remain unverified. Actual provider call/runtime cost includes earlier failed attempts and is not replaced with guessed counts.

Authoritative audit files: `parent-strategy-audit.json`, `parent-audio-audit.json`, `parent-rate-repeats/report.json`, `parent-rate-repeats/original-retrim.json`.