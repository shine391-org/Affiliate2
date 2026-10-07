# D/E independent QA adjudication

## D: raw structural candidate is not admissible

The 8-second variant removed only the item-count boundary and found one structural candidate: legacy25 chunks59+60. The live paired measurement was 12,520ms (independent A chunks) versus11,545ms (combined B), saving975ms and one provider call. This is a real measurement, NOT a recommended/accepted grouping.

Independent content QA rejects this merge. Source chunk59 ends with `强烈推荐转转` (recommending Zhuanzhuan); chunk60 changes to an age question. The source montage `transition59_60.jpg`, extracted from136s onward, visibly switches from promotional captions `你有闲置想卖` / `强烈推荐转转` to conversational captions `小羊你多大` / `我23了`. The location/participants remain the same, but a distinct discourse/sentence boundary is present despite absent terminal punctuation in ASR. Same speaker/voice and zero timestamp gap do NOT prove utterance continuity.

The raw group still requires1.6343x after canonical allocation, above the1.35 emergency ceiling. The raw hard GROUP count15→14 is a denominator merge, not one speech problem rescued: two failing groups became one still-failing group. No complete video was rescued.

After rejecting this one candidate, the tested8-second admissible grouping variant is identical to the existing planner on all8 sources. This is a NULL result for THIS variant/dataset, not a universal refutation of utterance synthesis or a test of a12-second cap.

Additional source-fidelity finding: chunk60 ASR says `这么小呀 你多大`, translated as `Nhỏ thế này à, em bao nhiêu tuổi`, while visible source caption says `小羊你多大` (addressing Xiaoyang, asking age). Source captions can themselves be wrong and no new ASR/listening adjudication was performed; this contradiction is a fidelity warning. A deterministic guard PASS is not semantic ground truth.

## E: scope and limits

`de/results.json` contains42 original-window overflows using stored REAL trimmed audio, plus current allocator replay and acoustic silence detection on all8 sources. Raw-before-trim is NULL for historical audio, not invented. The fresh A/B branch can fill raw/trim comparisons for its new leaf samples.

Acoustic silence was probed by FFmpeg at-35dB/min60ms. Source ambience/music can mask silence; zero detected silence is NOT proof of neighboring speech. Source word-anchor gap, allocator reserve and acoustic silence are separately reported.

Per-row `policy_contributors` are observable contributors, not a causal exclusive category or measured root-cause percentage. In particular, a speaker boundary blocks GROUPING; the allocator has no explicit speaker prohibition. Reserve is min(actual source gap,350ms), not gap_tolerance minus350ms. Allocation gains can be negative when normalizing overlaps, which should not be mislabeled recovered silence.

## Calibration correction

The previous planner-analysis.md claim that `DubbingRateCalibrator` is dead code is superseded: DubbingJobState.calibration() instantiates/observes it and the production executor persists its result. The review has not shown those ratios alter planning estimates. Distinguish live observation from adaptive planner consumption.

## Verification level

D: LIVE paired Edge TTS on one REJECTED candidate; accepted variant NULL.
E: REAL stored-audio probe/hash checks + LOCAL canonical allocator replay + REAL source acoustic silence probe.
Full alternative render/QC, listening naturalness, and8-source fresh pipeline baseline: NOT VERIFIED.