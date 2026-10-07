"""Shared fail-closed audit and budget checks for intentional Auto cue omission."""
from __future__ import annotations


def validate_translation_omissions(transcript, profile) -> set[str]:
    omitted = [s for s in transcript.segments if s.translation_status == 'omitted']
    if not omitted:
        return set()
    if profile.translation_automation != 'auto':
        raise ValueError('Strict translation cannot omit speech cues')
    records = transcript.metadata.get('translation_omissions')
    if not isinstance(records, dict):
        raise ValueError('Missing translation omission audit')
    for s in omitted:
        record = records.get(s.id)
        if (not isinstance(record, dict) or record.get('source_text') != s.source_text
                or record.get('source_start_ms') != s.source_start_ms
                or record.get('source_end_ms') != s.source_end_ms
                or s.source_end_ms <= s.source_start_ms
                or (s.translated_text or '').strip() or (s.tts_duration_ms or 0) != 0
                or s.target_start_ms is not None or s.target_end_ms is not None
                or not str(record.get('rejected_target_text') or '').strip()
                or not record.get('issues') or not record.get('reason')
                or type(record.get('attempts')) is not int or record['attempts'] < 2):
            raise ValueError(f'Invalid current omission audit for {s.id}')
    total = sum(max(0, s.source_end_ms-s.source_start_ms) for s in transcript.segments)
    duration = sum(s.source_end_ms-s.source_start_ms for s in omitted)
    limit = profile.translation_omission_max_ratio
    if (len(omitted) == len(transcript.segments) or total <= 0
            or len(omitted)/len(transcript.segments) > limit+1e-9
            or duration/total > limit+1e-9):
        raise ValueError('Translation omission budget exceeded; remaining speech required')
    return {s.id for s in omitted}
