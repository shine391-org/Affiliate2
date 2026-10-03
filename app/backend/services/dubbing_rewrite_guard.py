# SPDX-License-Identifier: AGPL-3.0-only
"""Code-level meaning guard for AI dubbing rewrites.

The dubbing rewriter is allowed to remove filler. It is never allowed to drop
the facts that make a sentence mean what a human approved: negation, numbers,
units, defined terms, and antonym flips.

This module is deliberately language-scoped. `target_language` has ~20 values,
so hardcoding Vietnamese would silently disable the guard for 19 of them. Each
language contributes its own negation/quantity markers and antonym pairs;
unknown languages still get the structural (language-neutral) protections.

Design rule: blocking a legitimate rewrite costs one extra TTS round; letting a
meaning-destroying rewrite through ships wrong content to a human reviewer who
may never speak the target language. Prefer blocking.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

# ── Language-scoped lexicons ──────────────────────────────────────────────
# Negation markers: dropping one of these removes a distinction.
_NEGATION: dict[str, tuple[str, ...]] = {
    "vietnamese": (
        "không", "chưa", "chẳng", "chả", "đừng", "chưa từng", "hề", "gì cả",
        "chả", "chẳng hề", "nào", "chẳng phải", "đâu có", "chưa bao giờ",
    ),
    "english": (
        "not", "no", "never", "none", "nothing", "neither", "nor", "without",
        "cannot", "can't", "don't", "doesn't", "didn't", "isn't", "aren't",
        "won't", "shouldn't", "wouldn't", "couldn't", "hardly", "barely",
    ),
    "spanish": (
        "no", "ni", "nunca", "jamás", "nada", "ninguno", "ninguna", "tampoco",
        "sin", "nadie", "nunca",
    ),
    "french": (
        "ne", "pas", "non", "jamais", "rien", "aucun", "aucune", "personne",
        "ni", "sans", "rien",
    ),
    "german": (
        "nicht", "kein", "keine", "keinen", "keinem", "keiner", "nie", "niemals",
        "nichts", "niemand", "ohne", "nicht",
    ),
    "italian": (
        "non", "no", "mai", "niente", "nessuno", "nulla", "né", "ne", "senza",
    ),
    "portuguese": (
        "não", "nao", "nunca", "jamais", "nada", "nenhum", "nenhuma", "nem",
        "sem", "ninguém",
    ),
    "dutch": (
        "niet", "geen", "geen", "nooit", "niets", "niemand", "zonder", "nergens",
    ),
    "polish": (
        "nie", "nie", "żaden", "żadna", "żadne", "nic", "nikt", "bez", "ani",
        "nie",
    ),
    "russian": (
        "не", "нет", "ни", "никогда", "ничего", "никто", "без", "нельзя",
    ),
    "arabic": (
        "لا", "لم", "لن", "ليس", "غير", "بدون", "ما", "مستحيل", "لا",
    ),
    "hindi": (
        "नहीं", "नहि", "कभी", "कुछ", "नहीं", "बिना", "ना", "मत",
    ),
    "turkish": ("değil", "yok", "asla", "hiç", "bir şey", "olmaz", "edemez"),
    "swedish": ("inte", "ingen", "aldrig", "inget", "ingen", "utan", "nej"),
    "danish": ("ikke", "ingen", "aldrig", "intet", "uden", "nej"),
    "norwegian": ("ikke", "ingen", "aldri", "ingenting", "uten", "nei"),
    "finnish": ("ei", "eivät", "ilman", "ei koskaan", "mikään", "ei mitään"),
    "japanese": (
        "ない", "ません", "ではない", "じゃない", "ありません", "なかった",
        "しない", "できない", "ないです", "ません",
    ),
    # Korean negation is agglutinative: 않습니다 / 않습니다 / 않는 are the same
    # root written differently, so several inflected forms must be listed.
    "korean": (
        "아니", "안", "없", "못", "않", "닙", "ㅂ니다",
        "지 않다", "안 되", "없다", "안은",
    ),
    "chinese": (
        "不", "没", "没有", "无", "非", "未", "别", "莫", "勿", "不是", "不会",
        "不能", "不要", "没什么",
    ),
}

# Units / measure words. Language-neutral where possible so an unmapped
# language still catches "5kg" -> "nặng".
_UNITS: tuple[str, ...] = (
    "kg", "km", "cm", "mm", "mg", "ml", "gb", "tb", "mb", "hz", "khz", "mhz",
    "%", "°", "kg", "lít", "lít", "mét", "gram", "tấn", "xu", "đồng", "vnd",
    "usd", "đô la", "phút", "giây", "giờ", "ngày", "tuần", "tháng", "năm",
    "lần", "phần", "người", "cách", "hàng", " mét",
)

# Antonym pairs per language: (a, b) where swapping one for the other
# reverses the claim.
_ANTONYMS: dict[str, tuple[tuple[str, str], ...]] = {
    "vietnamese": (
        ("chụp", "quay"), ("trước", "sau"), ("tăng", "giảm"),
        ("đắt", "rẻ"), ("đầu", "cuối"), ("mở", "đóng"),
        ("bật", "tắt"), ("có", "không"), ("đúng", "sai"),
        ("cao", "thấp"), ("trên", "dưới"), ("trong", "ngoài"),
        ("muốn", "không muốn"), ("luôn", "không bao giờ"),
        # Deliberately absent: dài<->ngắn, nhanh<->chậm, nhiều<->ít as LENGTH
        # adjectives. A rewrite exists to compress filler, and dropping a
        # descriptive "a very long sentence" is the intended behaviour, not a
        # meaning flip. Length pairs are only meaningful as a comparison claim,
        # which this guard cannot distinguish reliably; keeping them blocked
        # would reject every legitimate compression.
    ),
    "english": (
        ("before", "after"), ("more", "less"), ("increase", "decrease"),
        ("always", "never"), ("yes", "no"), ("on", "off"), ("up", "down"),
        ("open", "closed"), ("long", "short"), ("fast", "slow"),
        ("expensive", "cheap"), ("with", "without"),
    ),
    "spanish": (
        ("antes", "después"), ("más", "menos"), ("antes", "despues"),
        ("siempre", "nunca"), ("sí", "no"),
    ),
    "french": (
        ("avant", "après"), ("plus", "moins"), ("toujours", "jamais"),
        ("oui", "non"),
    ),
    "german": (
        ("vorher", "nachher"), ("mehr", "weniger"), ("immer", "nie"),
        ("ja", "nein"), ("vor", "nach"),
    ),
    "italian": (("prima", "dopo"), ("più", "meno"), ("sempre", "mai"), ("sì", "no")),
    "portuguese": (("antes", "depois"), ("mais", "menos"), ("sempre", "nunca"), ("sim", "não")),
    "russian": (("до", "после"), ("больше", "меньше"), ("всегда", "никогда"), ("да", "нет")),
    "japanese": (("前", "後"), ("多く", "少なく"), ("いつも", "一度も")),
    "korean": (("전", "후"), ("많이", "적게"), ("항상", "한번도")),
    "chinese": (("前", "后"), ("多", "少"), ("总是", "从不")),
}

# Tokens that carry no meaning on their own; losing them is legitimate.
_STOPWORDS: frozenset[str] = frozenset({
    # Vietnamese function words (kept short so content words are never dropped)
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "was", "were", "be", "it", "this", "that", "these", "those", "as", "at",
    "by", "with", "from", "và", "hoặc", "là", "của", "có", "được", "trong",
    "này", "đó", "khi", "để", "mà", "thì", "nhưng", "rất", "quá", "thật",
    "ra", "là", "đây", "ấy", "thế", "vậy", "một", "những", "các", "cho",
    # other languages
    "un", "une", "le", "la", "les", "des", "eine", "der", "die", "das",
    "un", "una", "el", "lo", "os", "as", "не", "и", "в", "на", "с",
})

_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# Hangul is included for the substring/containment path only: Korean negation is
# agglutinative, so the lexicon entry never survives whitespace tokenisation.
# Korean is NOT split into individual characters, because Korean particles bind
# to the preceding syllable and per-character matching would be noise.
_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")
_SCRIPT_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")
_NUMBER_RE = re.compile(r"\d")


def _norm_lang(target_language: str | None) -> str:
    return (target_language or "").strip().lower()


def _lexicon_for(lang: str) -> str:
    """Map a profile language name / code onto a lexicon key.

    Accepts both 'Vietnamese' and 'vi' so the guard works no matter which
    representation the caller holds.
    """
    if lang in _NEGATION:
        return lang
    from backend.core.tool_runners import _LANG_NAME_MAP
    for code, name in _LANG_NAME_MAP.items():
        if lang == code:
            key = name.lower()
            if key in _NEGATION:
                return key
    for key in _NEGATION:
        if key in lang:
            return key
    return ""


def _tokens(text: str) -> list[str]:
    """Lowercase word tokens; CJK runs are emitted as individual characters.

    CJK languages do not delimit words with spaces, so a whitespace tokeniser
    would see one giant token and silently match nothing.
    """
    text = unicodedata.normalize("NFKC", text or "").lower()
    out: list[str] = []
    for raw in re.findall(r"[^\W_]+", text, flags=re.UNICODE):
        if _CJK_RE.search(raw):
            out.extend(raw)
        else:
            out.append(raw)
    return out


def _content_tokens(tokens: Iterable[str]) -> set[str]:
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 1}


def _negation_hits(tokens: Iterable[str], lang_key: str, raw_text: str = "") -> set[str]:
    """Negation markers present in the text.

    CJK markers like 'ではありません' are multi-character and survive
    tokenisation only as single characters, so equality against the lexicon
    would never match. For those scripts we additionally test substring
    containment on the normalised raw text.
    """
    marks = _NEGATION.get(lang_key, ())
    hits = {t for t in tokens if t in marks}
    if raw_text and _SCRIPT_RE.search(raw_text):
        norm = unicodedata.normalize("NFKC", raw_text).lower()
        for mark in marks:
            if mark in norm:
                hits.add(mark)
    return hits


def check_rewrite_meaning(
    before: str,
    after: str,
    target_language: str | None = None,
) -> list[dict]:
    """Return violations for an AI rewrite. Empty list means safe to apply.

    `before` is the human-approved text, `after` is the model's proposal.
    The caller must keep `before` unchanged and refuse `after` when this
    returns a non-empty list.
    """
    before = (before or "").strip()
    after = (after or "").strip()
    if not before or not after:
        return []

    lang_key = _lexicon_for(_norm_lang(target_language))
    old_tokens = _tokens(before)
    new_tokens = _tokens(after)
    violations: list[dict] = []

    # 1. Negation must survive.
    old_neg = _negation_hits(old_tokens, lang_key, before)
    new_neg = _negation_hits(new_tokens, lang_key, after)
    for mark in sorted(old_neg - new_neg):
        violations.append({
            "kind": "negation_lost",
            "token": mark,
            "detail": f"negation {mark!r} present in approved text, absent from rewrite",
        })

    # 2. Numbers must survive. Counts carry facts a paraphrase cannot keep.
    old_nums = [t for t in old_tokens if _NUMBER_RE.search(t)]
    new_nums = [t for t in new_tokens if _NUMBER_RE.search(t)]
    for num in sorted(set(old_nums) - set(new_nums)):
        violations.append({
            "kind": "quantity_lost",
            "token": num,
            "detail": f"number {num!r} dropped by rewrite",
        })

    # 3. Units must survive.
    old_units = {t for t in old_tokens if t in _UNITS}
    new_units = {t for t in new_tokens if t in _UNITS}
    for unit in sorted(old_units - new_units):
        violations.append({
            "kind": "unit_lost",
            "token": unit,
            "detail": f"unit {unit!r} dropped by rewrite",
        })

    # 4. Defined terms: content bigrams present before and gone after.
    # Single dropped words are legitimate compression; a specific two-word
    # term vanishing is a term being replaced by a generic word.
    def bigrams(toks: list[str]) -> set[str]:
        content = _content_tokens(toks)
        return {" ".join(p) for p in zip(toks, toks[1:]) if p[0] in content and p[1] in content}

    lost_terms = sorted(bigrams(old_tokens) - bigrams(new_tokens))
    # Only flag a lost bigram when BOTH of its words were otherwise retained
    # somewhere in the sentence. Otherwise it is ordinary compression, not a
    # term being swapped for a generic synonym.
    new_content = _content_tokens(new_tokens)
    for term in lost_terms:
        words = term.split()
        if all(w in new_content for w in words):
            violations.append({
                "kind": "term_lost",
                "token": term,
                "detail": f"defined term {term!r} dropped by rewrite",
            })
            break

    # 5. Antonym flips: an approved word replaced by its opposite.
    # A flip is `a` present before, `b` (its opposite) introduced after.
    old_content = _content_tokens(old_tokens)
    for a, b in _ANTONYMS.get(lang_key, ()):
        if a in old_content and b in new_content and b not in old_content:
            violations.append({
                "kind": "antonym_flip",
                "token": b,
                "detail": f"rewrite replaced {a!r} with its opposite {b!r}",
            })
            break

    # De-duplicate by kind so one defect is reported once.
    seen: set[str] = set()
    unique: list[dict] = []
    for v in violations:
        if v["kind"] in seen:
            continue
        seen.add(v["kind"])
        unique.append(v)
    return unique