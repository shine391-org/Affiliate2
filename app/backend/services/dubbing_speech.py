# SPDX-License-Identifier: AGPL-3.0-only
"""C7 speaks approved C6 text; name localization belongs before review."""
from __future__ import annotations

from backend.services.dubbing_rewrite_guard import untranslated_han


async def prepare_speech(transcript, *, profile, runtime, **_kwargs):
    language = transcript.metadata.get("target_language")
    # Voice locale is only a fallback for older stored transcripts.
    if not language and str(runtime.default_voice).startswith("vi-"):
        language = "vi"
    missing = {seg.id: untranslated_han(seg.translated_text or "", language,
                   allowed=[*profile.protected_terms, *profile.glossary.values()])
               for seg in transcript.segments if seg.translation_status == "success"}
    missing = {key: value for key, value in missing.items() if value}
    if missing:
        raise ValueError(f"Unlocalized Vietnamese text: rerun translation before dubbing ({list(missing)})")
    transcript.metadata.pop("speech_preparation", None)
    return []
