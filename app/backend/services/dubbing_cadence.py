# SPDX-License-Identifier: AGPL-3.0-only
"""Measured cue placement inside one TTS chunk; no text or provider policy changes."""
from __future__ import annotations
import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path


def word_tokens(text: str) -> list[str]:
    return re.findall(r'[^\W_]+',unicodedata.normalize('NFC',text).casefold(),re.UNICODE)


def read_cue_spans(audio: Path, texts: list[str], duration_ms: int) -> list[dict] | None:
    """Only use exact full-text, hash-bound provider timings; never guess a cut."""
    side=audio.with_suffix(audio.suffix+'.words.json')
    if not side.is_file():
        return None
    try:
        data=json.loads(side.read_text(encoding='utf-8'))
        if (data.get('version') != 1 or data.get('timing_basis') != 'provider_word_boundary'
                or data.get('audio_sha256') != hashlib.sha256(audio.read_bytes()).hexdigest()
                or word_tokens(data.get('text','')) != word_tokens(' '.join(texts))):
            return None
        expected=[word_tokens(t) for t in texts]
        if any(not t for t in expected):return None
        words=[];previous_start=-1
        for w in data['words']:
            start,end=w['start_ms'],w['end_ms']
            if (isinstance(start,bool) or isinstance(end,bool) or not isinstance(start,(int,float))
                    or not isinstance(end,(int,float)) or not math.isfinite(start) or not math.isfinite(end)
                    or start<0 or end<=start or start<previous_start or end>duration_ms+30):return None
            tokens=word_tokens(w['word'])
            # A multi-token provider event cannot identify a cue split inside it.
            if len(tokens)!=1:return None
            words.append({'word':tokens[0],'start_ms':float(start),'end_ms':min(float(end),duration_ms)})
            previous_start=start
        if [w['word'] for w in words] != [t for cue in expected for t in cue]:return None
        indexes=[];cursor=0
        for cue in expected:
            indexes.append((cursor,cursor+len(cue)-1));cursor+=len(cue)
        cuts=[0.0]
        for first,_ in indexes[1:]:
            left,right=words[first-1]['end_ms'],words[first]['start_ms']
            if left>right or (left+right)/2<=cuts[-1]:return None
            cuts.append((left+right)/2)
        cuts.append(float(duration_ms))
        return [{'raw_start_ms':cuts[i],'raw_end_ms':cuts[i+1],
                 'speech_start_ms':words[first]['start_ms'],
                 'speech_end_ms':words[last]['end_ms']}
                for i,(first,last) in enumerate(indexes)]
    except (OSError,ValueError,TypeError,KeyError,IndexError,AttributeError):
        return None


def place_cue_spans(items,chunk,spans: list[dict],*,speed_ceiling: float, anchor_scale: float = 1.0) -> list[dict]:
    """Preserve all PCM partitions, choose one bounded rate and add source pauses.

    Returns exact raw partition/cue placement. Raises when measured cadence is
    infeasible under the caller's ceiling; never hides it by overlapping speech.
    Source anchors here are already on the output/retimed timeline.
    """
    if len(items)!=len(spans) or not spans:raise ValueError('cadence span count mismatch')
    anchors=[float(i.original_start_ms)*anchor_scale for i in items]
    if any(b<=a for a,b in zip(anchors,anchors[1:])):raise ValueError('non-increasing cadence anchors')
    speed=1.0
    for i,span in enumerate(spans):
        suffix=span['raw_end_ms']-span['speech_start_ms']
        if i+1<len(spans):
            next_prefix=spans[i+1]['speech_start_ms']-spans[i+1]['raw_start_ms']
            budget=anchors[i+1]-anchors[i]
            speed=max(speed,(suffix+next_prefix)/budget)
        else:
            budget=chunk.end_ms-anchors[i]
            if budget<=0:raise ValueError('no final cadence window')
            speed=max(speed,suffix/budget)
    if not math.isfinite(speed) or speed>speed_ceiling+1e-9:
        raise ValueError(f'measured cadence requires {speed:.4f}x, ceiling {speed_ceiling:.4f}x')
    result=[];previous_end=float(chunk.start_ms)
    for item,span,anchor in zip(items,spans,anchors):
        prefix=span['speech_start_ms']-span['raw_start_ms']
        start=max(float(chunk.start_ms),anchor-prefix/speed)
        end=start+(span['raw_end_ms']-span['raw_start_ms'])/speed
        if start<previous_end-0.01 or end>chunk.end_ms+0.01:
            raise ValueError('measured cadence cannot preserve PCM without overlap')
        onset=start+prefix/speed
        speech_end=start+(span['speech_end_ms']-span['raw_start_ms'])/speed
        result.append({**span,'segment_id':item.segment_id,'start_ms':start,'end_ms':end,
                       'target_start_ms':round(onset),'target_end_ms':round(speech_end),'speed_factor':speed})
        previous_end=end
    return result
