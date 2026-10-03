# SPDX-License-Identifier: AGPL-3.0-only
"""Code-level meaning guard for AI dubbing rewrites.

The dubbing rewriter is allowed to remove filler. It is never allowed to drop
the facts that make a sentence mean what a human approved: negation, numbers,
units, explicit protected terms, and antonym flips. C6 also checks cross-language
invariants before accepting a translation.

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
from collections import Counter
from typing import Iterable

# ── Language-scoped lexicons ──────────────────────────────────────────────
# Negation markers: dropping one of these removes a distinction.
_NEGATION: dict[str, tuple[str, ...]] = {
    "vietnamese": (
        "không", "chưa", "chẳng", "chả", "đừng", "chưa từng", "không hề", "không có gì cả",
        "chả", "chẳng hề", "chẳng phải", "đâu có", "chưa bao giờ",
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
        "नहीं", "नहि", "कभी नहीं", "कुछ नहीं", "बिना", "ना", "मत",
    ),
    "turkish": ("değil", "yok", "asla", "hiç", "olmaz", "edemez"),
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
        "아니", "아닙", "안", "없", "못", "않",
        "지 않다", "안 되", "없다", "안은",
    ),
    "chinese": (
        "不", "没", "没有", "无", "非", "未", "别", "莫", "勿", "不是", "不会",
        "不能", "不要", "没什么",
    ),
}

# Antonym pairs per language: (a, b) where swapping one for the other
# reverses the claim.
_ANTONYMS: dict[str, tuple[tuple[str, str], ...]] = {
    "vietnamese": (
        ("chụp", "quay"), ("trước", "sau"), ("tăng", "giảm"),
        ("đắt", "rẻ"), ("đầu", "cuối"), ("mở", "đóng"),
        ("bật", "tắt"), ("có", "không"), ("đúng", "sai"),
        ("cao", "thấp"), ("trên", "dưới"), ("trong", "ngoài"),
        ("muốn", "không muốn"), ("luôn", "không bao giờ"),
        ("dài", "ngắn"), ("nhanh", "chậm"), ("nhiều", "ít"),
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

# Only explicit user terms are protected. Ordinary adjacent words are not a glossary.
_SCRIPT_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
_LANGUAGE_CODES = dict(zip(
    "vi en es fr de it pt nl pl ru ar hi tr sv da no nb fi ja ko zh".split(),
    "vietnamese english spanish french german italian portuguese dutch polish russian arabic hindi turkish swedish danish norwegian norwegian finnish japanese korean chinese".split(),
))
_NUMBER_RE = re.compile(r"[+-]?\d+(?:[.,]\d+)*(?:/\d+)?")
_HAN_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\U00020000-\U0003134f]+")
_UNIT_ALIASES = {
    "kg": ("kg", "kilogram", "kilograms", "kilôgam", "公斤", "千克"),
    "g": ("g", "gram", "grams", "gam", "克"),
    "mg": ("mg", "milligram", "milligrams", "毫克"),
    "km": ("km", "kilometer", "kilometers", "kilometre", "公里", "千米"),
    "m": ("m", "meter", "meters", "metre", "mét", "米"),
    "cm": ("cm", "centimeter", "centimeters", "centimetre", "厘米"),
    "mm": ("mm", "millimeter", "millimeters", "millimetre", "毫米"),
    "ml": ("ml", "milliliter", "milliliters", "毫升"),
    "l": ("l", "liter", "liters", "litre", "lít", "升"),
    "second": ("s", "second", "seconds", "giây", "秒"),
    "minute": ("minute", "minutes", "phút", "分钟", "分鐘"),
    "hour": ("hour", "hours", "giờ", "小时", "小時"),
    "day": ("day", "days", "ngày", "天"),
    "week": ("week", "weeks", "tuần", "星期"),
    "month": ("month", "months", "tháng", "个月", "個月"),
    "year": ("year", "years", "năm", "年"),
    "age": ("tuổi", "years old", "year old", "岁", "歲", "歳"),
    "percent": ("%", "percent", "percentage", "phần trăm", "百分之"),
    "celsius": ("°c", "celsius", "độ c", "摄氏度"),
    "usd": ("usd", "$", "dollar", "dollars", "đô la", "美元"),
    "vnd": ("vnd", "đồng", "越南盾"),
    "lb": ("lb", "lbs", "pound", "pounds", "磅"),
    "oz": ("oz", "ounce", "ounces", "盎司"),
    "degree": ("°", "degree", "degrees", "độ", "度"),
    "fahrenheit": ("°f", "fahrenheit", "độ f", "华氏度"),
    "eur": ("eur", "€", "euro", "euros", "欧元"),
    "cny": ("cny", "rmb", "yuan", "nhân dân tệ", "元", "块", "塊"),
    **{unit: (unit,) for unit in ("gb", "mb", "tb", "hz", "khz", "mhz")},
}
# Cross-language polarity checks for common operational claims in C6.
_TRANSLATION_POLARITY = (
    ({"en": "increase", "vi": "tăng", "zh": "增加"},
     {"en": "decrease", "vi": "giảm", "zh": "减少"}),
    ({"en": "before", "vi": "trước", "zh": "之前"},
     {"en": "after", "vi": "sau", "zh": "之后"}),
    ({"en": "turn on", "vi": "bật", "zh": "打开"},
     {"en": "turn off", "vi": "tắt", "zh": "关闭"}),
)


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text or "").casefold().split())


def _lexicon_for(language: str | None) -> str:
    key = (language or "").strip().lower().split("-")[0]
    return _LANGUAGE_CODES.get(key, key if key in _NEGATION else "")


def _phrase_pattern(phrase: str) -> str:
    phrase = _normalize(phrase)
    pattern = r"\s+".join(re.escape(part) for part in phrase.split())
    if phrase and not _SCRIPT_RE.search(phrase):
        if phrase[0].isalnum():
            pattern = r"(?<![^\W_])" + pattern
        if phrase[-1].isalnum():
            pattern += r"(?![^\W_])"
    return pattern


def _contains(text: str, phrase: str) -> bool:
    return bool(phrase and re.search(_phrase_pattern(phrase), _normalize(text)))


def _negations(text: str, language: str | None) -> int:
    spans = set()
    text = _normalize(text)
    for term in _NEGATION.get(_lexicon_for(language), ()):
        spans.update(match.span() for match in re.finditer(_phrase_pattern(term), text))
    # A phrase and its constituent marker count as one negation.
    return sum(not any(a <= start and end <= b and (a, b) != (start, end)
                       for a, b in spans) for start, end in spans)


def _numbers(text: str) -> Counter:
    return Counter(_NUMBER_RE.findall(_numeric_text(text)))


def _numeric_text(text: str) -> str:
    """Chinese written quantities become digits; a name such as 张三 does not.

    C6 requests Arabic digits in the target. Only a following measurement or
    age marker makes an unseparated Chinese numeral a quantity.
    """
    text = _normalize(text)
    digits = {char: value for value, char in enumerate("零一二三四五六七八九")}
    digits.update({"〇": 0, "两": 2, "兩": 2})
    scales = {"十": 10, "百": 100, "千": 1000, "万": 10000, "萬": 10000}

    def convert(match):
        token = match.group()
        if not any(char in scales for char in token):
            return "".join(str(digits[char]) for char in token)
        total = section = current = 0
        for char in token:
            if char in digits:
                current = digits[char]
            elif scales[char] == 10000:
                total += (section + current) * 10000
                section = current = 0
            else:
                section += (current or 1) * scales[char]
                current = 0
        return str(total + section + current)

    return re.sub(r"(?<![\d零〇一二三四五六七八九两兩十百千万萬])[零〇一二三四五六七八九两兩十百千万萬]+?(?=\s*(?:岁|歲|歳|年|个|個|公斤|千克|克|公里|千米|米|升|秒|分钟|小時|小时|天|元|块|塊|%))", convert, text)


def _unit_spans(text: str) -> list[tuple[int, int, str]]:
    text = _numeric_text(text)
    spans = set()
    for unit, aliases in _UNIT_ALIASES.items():
        for alias in aliases:
            # SI suffixes can be attached to a number (5kg); single letters
            # are units only in an explicit numeric measurement.
            if re.fullmatch(r"[a-z]+", alias):
                for match in re.finditer(r"\d\s*(" + re.escape(alias) + r")(?![a-z])", text):
                    spans.add((*match.span(1), unit))
                if len(alias) == 1:
                    continue
            for match in re.finditer(_phrase_pattern(alias), text):
                # Names such as Ngô Tiểu Đồng / 马克 and ordinary words such
                # as "năm" are not currency/grams/years without a quantity.
                if _SCRIPT_RE.search(alias) or alias in {"đồng", "năm", "gam", "độ", "day"}:
                    before = re.search(r"\d\s*$", text[:match.start()])
                    after = re.match(r"\s*\d", text[match.end():]) if (
                        unit in {"usd", "eur", "vnd", "cny", "percent"} and alias not in {"đồng", "元", "块", "塊"}) else None
                    if not before and not after:
                        continue
                spans.add((*match.span(), unit))
    # 千克 contains 克 and °C contains °: use the complete unit, not both.
    return [(start, end, unit) for start, end, unit in spans
            if not any(a <= start and end <= b and (a, b) != (start, end) for a, b, _ in spans)]


def _units(text: str) -> set[str]:
    return {unit for _, _, unit in _unit_spans(text)}


def _measurements(text: str) -> Counter:
    text = _numeric_text(text)
    measurements = Counter()
    for start, end, unit in _unit_spans(text):
        before = re.search(r"(" + _NUMBER_RE.pattern + r")\s*$", text[:start])
        if before:
            measurements[(before.group(1), unit)] += 1
        elif unit in {"percent", "usd", "eur", "vnd"}:
            # Currency/percentage markers can precede or follow the value.
            after = re.match(r"\s*(" + _NUMBER_RE.pattern + r")", text[end:])
            if after:
                measurements[(after.group(1), unit)] += 1
    return measurements


def _polarity_hits(text: str, first: str, second: str) -> set[str]:
    hits = set()
    for term, other in ((first, second), (second, first)):
        # 'muốn' inside 'không muốn' is not an affirmative assertion.
        scrubbed = re.sub(_phrase_pattern(other), " ", _normalize(text)) if term in other else text
        if _contains(scrubbed, term):
            hits.add(term)
    return hits


def _invariants(before: str, after: str, source_language: str | None,
                target_language: str | None) -> list[dict]:
    issues = []
    old_neg, new_neg = _negations(before, source_language), _negations(after, target_language)
    if old_neg != new_neg:
        issues.append({"kind": "negation_lost" if old_neg > new_neg else "negation_added",
                       "detail": f"negation count changed: {old_neg} -> {new_neg}"})
    if _numbers(before) != _numbers(after):
        issues.append({"kind": "quantity_lost", "detail": "numeric values/counts changed"})
    if _units(before) != _units(after) or _measurements(before) != _measurements(after):
        issues.append({"kind": "unit_lost", "detail": "measurement units or numeric/unit bindings changed"})
    # Punctuation carries the speaker's intent. A question or exclamation must
    # not turn into a flat assertion during translation/compression.
    for mark, kind in (("?", "question_changed"), ("!", "exclamation_changed")):
        if bool(mark in _normalize(before)) != bool(mark in _normalize(after)):
            issues.append({"kind": kind, "detail": "source speech act changed"})
    return issues


def untranslated_han(text: str, target_language: str | None, *, allowed: Iterable[str] = ()) -> list[str]:
    if _lexicon_for(target_language) != "vietnamese":
        return []
    for term in sorted(allowed, key=len, reverse=True):
        if term:
            text = text.replace(term, "")
    return list(dict.fromkeys(_HAN_RE.findall(text)))


def check_rewrite_meaning(before: str, after: str, target_language: str | None = None,
                          *, protected_terms: Iterable[str] = ()) -> list[dict]:
    """Reject deterministic invariant violations before accepting any C7 text."""
    if not before.strip() or not after.strip():
        return []
    issues = _invariants(before, after, target_language, target_language)
    for term in protected_terms:
        if _contains(before, term) and not _contains(after, term):
            issues.append({"kind": "term_lost", "token": term, "detail": "explicit protected term lost"})
    for first, second in _ANTONYMS.get(_lexicon_for(target_language), ()):
        old = _polarity_hits(before, first, second)
        new = _polarity_hits(after, first, second)
        if (first in old and second in new and second not in old) or (second in old and first in new and first not in old):
            issues.append({"kind": "antonym_flip", "detail": f"polarity changed: {first} / {second}"})
    return issues


def check_translation_meaning(before: str, after: str, source_language: str | None,
                              target_language: str | None, *, glossary: dict[str, str],
                              protected_terms: Iterable[str] = ()) -> list[dict]:
    """C6 cross-language checks; do not compare literal source/target wording."""
    issues = _invariants(before, after, source_language, target_language)
    remaining = untranslated_han(after, target_language, allowed=[*protected_terms, *glossary.values()])
    if remaining:
        issues.append({"kind": "unlocalized_text", "tokens": remaining, "detail": "Vietnamese captions/speech still contain untranslated Han text"})
    for source, target in glossary.items():
        if _contains(before, source) and not _contains(after, target):
            issues.append({"kind": "term_lost", "token": target, "detail": "glossary translation missing"})
    for term in protected_terms:
        if _contains(before, term) and not _contains(after, term):
            issues.append({"kind": "term_lost", "token": term, "detail": "explicit protected term lost"})
    source_code = next((code for code, name in _LANGUAGE_CODES.items() if name == _lexicon_for(source_language)), "")
    target_code = next((code for code, name in _LANGUAGE_CODES.items() if name == _lexicon_for(target_language)), "")
    for positive, negative in _TRANSLATION_POLARITY:
        old_a, old_b = positive.get(source_code, ""), negative.get(source_code, "")
        new_a, new_b = positive.get(target_code, ""), negative.get(target_code, "")
        if ((_contains(before, old_a) and not _contains(before, old_b) and _contains(after, new_b))
                or (_contains(before, old_b) and not _contains(before, old_a) and _contains(after, new_a))):
            issues.append({"kind": "antonym_flip", "detail": "translated polarity reversed"})
    return issues
