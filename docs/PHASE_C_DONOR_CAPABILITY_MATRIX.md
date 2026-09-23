# Phase C Donor Capability Matrix

This matrix tracks **quality mechanisms**, not a goal of cloning every donor UI
or every commercial/provider integration.

| Quality capability | Donor evidence | Affiliate2 status |
|---|---|---|
| Source-best media / minimum-loss render | Affiliate2 C1 + donor media workflows | IMPLEMENTED |
| Whisper transcription with word timestamps | VideoLingo | IMPLEMENTED |
| WhisperX forced alignment | VideoLingo | IMPLEMENTED |
| Optional speaker diarization | VideoLingo / SmartSub | IMPLEMENTED |
| Speaker-to-cue overlap assignment | SmartSub | IMPLEMENTED |
| Word-tier semantic boundary refinement | SmartSub / VideoLingo | IMPLEMENTED |
| AI segmentation cannot rewrite source | SmartSub-style validation | IMPLEMENTED |
| Contextual/glossary translation | VideoLingo / SmartSub | IMPLEMENTED |
| Display units separate from dub units | VideoLingo | IMPLEMENTED |
| Short-cue merge + chunk planner | OpenCreator | IMPLEMENTED |
| Hard speaker/voice chunk boundaries | donor multi-speaker behavior | IMPLEMENTED |
| Borrow unused silence | VideoLingo / SmartSub timing | IMPLEMENTED |
| Natural-rate TTS + measured duration | OpenCreator / VideoLingo | IMPLEMENTED |
| Rewrite/regenerate overlong speech | OpenCreator | IMPLEMENTED |
| Post-TTS FFmpeg atempo | OpenCreator / VideoLingo | IMPLEMENTED |
| Bounded last-resort video retime | pyVideoTrans concept (reference only) | IMPLEMENTED without GPL code |
| Demucs vocal/background separation | YouDub / VideoLingo | IMPLEMENTED |
| Clean BGM + target dub | YouDub | IMPLEMENTED |
| Long-audio chunk/overlap/crossfade | YouDub | IMPLEMENTED |
| HQ cloud TTS | SmartSub / VideoLingo provider breadth | IMPLEMENTED: OpenAI, Azure, ElevenLabs |
| OpenAI-compatible/local TTS endpoint | SmartSub provider architecture | IMPLEMENTED |
| Local voice cloning | VideoLingo | IMPLEMENTED for donor-compatible GPT-SoVITS EN/ZH; NOT claimed for Vietnamese |
| Automatic reference audio extraction | VideoLingo | IMPLEMENTED global/per-speaker |
| Multi-speaker voice mapping | donor speaker-aware dubbing | IMPLEMENTED |
| Persisted stage artifacts | YouDub / OpenCreator | IMPLEMENTED |
| Resume/rerun from stage | YouDub / OpenCreator | IMPLEMENTED |
| Batch uses same quality engine | mature automation pipelines | IMPLEMENTED |
| Dubbing/QC report | OpenCreator / SmartSub | IMPLEMENTED |
| Exception queue | SmartSub-style review workflow | IMPLEMENTED |
| Targeted translation/timing/voice override | SmartSub/OpenCreator editor behaviors | IMPLEMENTED |
| Targeted chunk regeneration | SmartSub/OpenCreator | IMPLEMENTED |
| One final visual encode | donor quality principle + Affiliate2 C1 | IMPLEMENTED |

## Intentionally not a core requirement

### Lip-sync
Lip-sync is a conditional visual enhancement for face-on-camera material, not a
mechanism required by these donors to produce a high-quality localized dub.
Making every product/B-roll video run a face model would increase artifacts and
compute without improving most Affiliate2 videos. It can be added later as an
optional winner-video enhancement.

### Every donor's TTS vendor button
Provider **quality tiers and extensibility** are required; duplicating every
vendor is not. Affiliate2 already has free fallback, HQ cloud and generic
OpenAI-compatible/local paths. GPT-SoVITS cloning is explicitly scoped to its
donor-compatible EN/ZH language contract rather than falsely advertised for Vietnamese. More provider adapters can
be added without changing C7 timing or C8 mixing.

### GPL implementation copy
pyVideoTrans remains reference-only. Affiliate2 implements its bounded-retiming
concept independently and does not copy GPL code into the AGPL application.
