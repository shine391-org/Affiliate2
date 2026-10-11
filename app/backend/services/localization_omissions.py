"""Shared fail-closed audit and budget checks for intentional Auto cue omission."""
from __future__ import annotations


def has_cue_local_repair_evidence(segment, record) -> bool:
    """Legacy dispatch counts cannot certify this cue's final repair candidate."""
    if not isinstance(record, dict):
        return False
    issues = record.get('issues')
    return (
        record.get('segment_id') == segment.id
        and record.get('source_text') == segment.source_text
        and record.get('source_start_ms') == segment.source_start_ms
        and record.get('source_end_ms') == segment.source_end_ms
        and record.get('speaker_id') == segment.speaker_id
        and record.get('origin') == 'semantic_repair'
        and record.get('repair_attempted') is True
        and type(record.get('semantic_attempts')) is int
        # Initial proposal + one repair + at most seven configured fallbacks.
        and 2 <= record['semantic_attempts'] <= 9
        and isinstance(record.get('target_text'), str)
        and bool(record['target_text'].strip())
        and isinstance(issues, list) and bool(issues)
        and all(isinstance(issue, dict) and isinstance(issue.get('kind'), str)
                and bool(issue['kind']) for issue in issues)
        and record.get('reasons') == [issue['kind'] for issue in issues]
        and record['target_text'] == (record.get('rejected_target_text')
            if segment.translation_status == 'omitted' else segment.translated_text)
    )


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
                or not has_cue_local_repair_evidence(s, record)):
            raise ValueError(f'Invalid current omission audit for {s.id}')
    total = sum(max(0, s.source_end_ms-s.source_start_ms) for s in transcript.segments)
    duration = sum(s.source_end_ms-s.source_start_ms for s in omitted)
    limit = profile.translation_omission_max_ratio
    if (len(omitted) == len(transcript.segments) or total <= 0
            or len(omitted)/len(transcript.segments) > limit+1e-9
            or duration/total > limit+1e-9):
        raise ValueError('Translation omission budget exceeded; remaining speech required')
    return {s.id for s in omitted}

