# Timing root-cause experiment — review checkpoint

**Not a completed production experiment. C and the final combined report remain incomplete at this packaging checkpoint.** No production timing redesign is justified by this snapshot.

## What is packaged

- `AB_CHECKPOINT.md`: independent A/B results, limits and anomaly repeat evidence.
- `D_ADJUDICATION.md`: rejected structural grouping candidate and read-only E limits.
- `checkpoint.json`: compact numeric results without private filesystem paths.
- `scripts/diagnostics/timing_root_cause/`: artifact audit, canonical allocator/retime replay, isolated LIVE anomaly-repeat and D/E harnesses, 19 focused evidence tests.

The preceding code/tests commit packages **pre-existing pending production work**, not a new fix selected from this experiment. It includes measured cue cadence/native word metadata, C7 rewrite-role/recovery handling, bounded transport split and Auto translation-omission audit. Translation omission of an unreadable rejected candidate is distinct from generic timing omission; this experiment does not validate a production omission strategy or authorize a new one. Strict translation remains the default. Human review remains required for relevant warnings.

## Reproduction with private evidence

Media, audio, manifests, source transcripts, provider logs and local databases are intentionally NOT committed. Clone alone cannot reproduce LIVE measurements without those inputs. Provision the original private artifact bundle, including:

- `chunk-corpus.json`, `dataset.json`, original source/audio files and manifests referenced by the corpus;
- `ab/results/{baseline,experiment_a,experiment_b}.jsonl`, `ab/audio/`, `ab/normalized/`;
- `de/results.json`, `de/silence-probes.jsonl` and D paired audio;
- `ab/parent-rate-repeats/{report,original-retrim}.json` and retained audio.

Set `TIMING_ARTIFACTS_ROOT` to that **external** bundle root. Absolute source references inside the private corpus must point to the real relocated evidence. Do not point the output directory at production storage. Each harness redirects backend settings to its own isolated data directory with ephemeral keys before importing backend code.

Example from Git Bash at the repository root:

```bash
export TIMING_ARTIFACTS_ROOT='D:/private/timing-root-cause'
.venv/Scripts/python.exe -E -X utf8 scripts/diagnostics/timing_root_cause/ab_parent_audit.py
```

Then run focused tests from the diagnostics directory:

```bash
cd scripts/diagnostics/timing_root_cause
../../../.venv/Scripts/python.exe -E -X utf8 -m unittest test_ab_parent_audit test_diagnostic_corpus test_diagnostic_de -v
```

`ab_parent_audit.py` reads measured audio, validates and writes local audits; it does NOT make provider calls. `ab_repeat_anomalies.py` performs 8 fresh Edge calls and preserves original A evidence; fresh repeat output uses dedicated filenames. `de_experiment.py` performs fresh paired TTS/source silence probes and writes raw structural results. Its one raw grouping candidate is **REJECTED** by the separate content adjudication; structural eligibility alone is not semantic acceptance. Do not use the raw D grouping counts as a production rescue metric. These LIVE scripts require network access and FFmpeg/ffprobe on PATH.

The historical source-specific tests deliberately assert this exact 8-source/154-chunk dataset and the original five regression IDs. They are not a general corpus acceptance framework.

## Verification levels

- Pending production code: 227 focused tests passed in an isolated data/temp directory. Some suites use provider mocks; no claim of complete LIVE production QA is made from this gate.
- A/B evidence: LIVE leaf TTS, 714 real audio files audited, LOCAL canonical timing projection. No render/QC automation or auditory naturalness claim.
- D: LIVE paired TTS on a rejected candidate; admissible tested grouping variant is NULL.
- E: real stored audio/source acoustic silence and LOCAL existing allocator replay; no new allocator.
- C: incomplete, not included as a finished result. A new delegation is separate from the failed original batch.

Only focused diagnostics and related localization suites were run; no full test suite. No media assets, credential backup, cookie or local database should be staged.
