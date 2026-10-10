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
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Iterable, Sequence

# ── Language-scoped lexicons ──────────────────────────────────────────────
# Negation markers: dropping one of these removes a distinction.
_NEGATION: dict[str, tuple[str, ...]] = {
    "vietnamese": (
        "không", "chưa", "chẳng", "chả", "đừng", "chưa từng", "không hề", "không có gì cả",
        "chả", "chẳng hề", "chẳng phải", "đâu có", "chưa bao giờ", "cấm",
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
        "不能", "不要", "没什么", "禁止", "严禁", "嚴禁", "不许", "不許",
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
    "week": ("week", "weeks", "tuần", "星期", "个星期", "個星期", "周", "週"),
    "month": ("month", "months", "tháng", "个月", "個月", "月"),
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

# ── Semantic-group / context contract (plan VI, Task C) ─────────────────────
# A subtitle cue is a display unit, not a semantic unit. These types let a
# caller hand the guard the surrounding cues so an incomplete sentence can be
# judged as the sentence it belongs to, WITHOUT any of it being required:
# omitting the context reproduces the previous single-cue behaviour exactly.

@dataclass(frozen=True)
class GuardContext:
    """Optional neighbouring-cue context for one cue under test.

    `source_before` / `source_after` / `target_before` / `target_after` are the
    immediately adjacent cues in the same language as the segment being checked.
    They may license a reading that the cue alone cannot support (an elided age
    whose question lives in the previous cue); they can never license a changed
    fact, because a licence must name the fact it applies to.
    """
    source_before: str = ""
    source_after: str = ""
    target_before: str = ""
    target_after: str = ""
    group_id: str | None = None

    def source_window(self) -> str:
        return f"{self.source_before} {self.source_after}".strip()

    def target_window(self) -> str:
        return f"{self.target_before} {self.target_after}".strip()

    def window(self) -> str:
        return f"{self.source_window()} {self.target_window()}".strip()


@dataclass(frozen=True)
class GuardSegment:
    """One cue inside a semantic validation group."""
    id: str
    source_text: str
    translated_text: str = ""


def build_guard_context(segments: Sequence[GuardSegment | dict], index: int,
                        *, group_id: str | None = None) -> GuardContext:
    """Context for `segments[index]` from its immediate neighbours only.

    `segments` may be GuardSegment objects or plain mappings with the keys
    `id`, `source_text` and `translated_text`. A context never crosses a
    speaker change: callers build the group, this helper only reads neighbours.
    """
    rows = list(segments)
    if not 0 <= index < len(rows):
        raise IndexError(index)

    def field(row, name):
        return row[name] if isinstance(row, dict) else getattr(row, name)

    def pair(position):
        if not 0 <= position < len(rows):
            return "", ""
        row = rows[position]
        return field(row, "source_text") or "", field(row, "translated_text") or ""

    before_source, before_target = pair(index - 1)
    after_source, after_target = pair(index + 1)
    if group_id is None and len(rows) > 1:
        group_id = f"group-{field(rows[0], 'id')}-{field(rows[-1], 'id')}"
    return GuardContext(source_before=before_source, source_after=after_source,
                        target_before=before_target, target_after=after_target,
                        group_id=group_id)


# Quantity roles. A count and an indefinite article both surface as '1', but
# only one of them is a claim about the world: 'tự chụp 1 tấm ảnh' commits to a
# single photo, '1 khuôn mặt' names a face without counting anything. Classifying
# the role before comparing is what separates seg-0008 (must stay blocked) from
# seg-0066 (uncertain, reviewable).
ROLE_CARDINAL = "cardinal"
ROLE_ARTICLE = "article"
ROLE_MEASUREMENT = "measurement"
ROLE_ORDINAL = "ordinal"

# Numerals followed by a noun but NOT by a classifier/measure word are
# indefinite articles on both sides of this language pair. Requiring the value
# to be exactly one keeps '3 khuôn mặt' a genuine count.
_ARTICLE_NUMERALS = ("một", "1", "mốt", "一")

# Chinese clause particles that make a bare '我23了' an elided statement about
# age rather than an unfinished number: 了 closes the clause, and the speaker
# refers to themselves. Compiled lazily because _ZH_NUMBER is defined below.
_ZH_ELIDED_AGE = None
# A neighbouring cue that actually asks for the age. Strict: the licence needs
# the question, not merely a cue that happens to contain a number.
_AGE_QUESTION_ZH = re.compile(r"多大|年龄|年齡|几岁|幾歲|多少岁|多少歲|年纪|年紀|幾年")
_AGE_QUESTION_VI = re.compile(r"bao nhiêu tuổi|mấy tuổi|bao nhiêu năm|tuổi gì")
# Rhetorical/standalone interrogatives whose position is not clause-final: 哪来
# ('where did that come from') is a complete question even with no 吗 and no
# closing punctuation, which is exactly what a cue boundary produces. Both the
# simplified and traditional spellings of 来 are listed.
_ZH_WHENCE = re.compile(r"哪\s*[裡里儿兒]?\s*[来來]|哪\s*[裡里儿兒]?\s*[来來]的?")


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
    if _lexicon_for(language) == "chinese":
        # These lexical words are not clause negators. Mask only their internal
        # morphemes; a preceding 不是/不要 must remain visible. 不准 is NOT
        # masked: it is a prohibition, also expressed by Vietnamese 'cấm'.
        text = re.sub(r"无孔不入|無孔不入|不约而同|不約而同|不知不觉|不知不覺", " ", text)
        # 非常 is an intensifier ('very'), not a clause negator. A-not-A
        # alternations (喜不喜欢) are one question, not a second negation;
        # the interrogative meaning is checked by _translation_question.
        text = re.sub(r"非常|不错|不錯|喜不喜欢|喜不喜歡|好不好|有没有|有沒有|是不是|"
                      r"能不能|可不可以|会不会|會不會|要不要|行不行|对不对|對不對", " ", text)
    elif _lexicon_for(language) == "french":
        # ne ... pas/jamais/rien is one grammatical negation. Mask only
        # the paired prefix within its clause, retaining the negative head.
        text = re.sub(r"(?<!\w)ne(?!\w)(?=[^.!?;:\n]{0,120}(?<!\w)"
                      r"(?:pas|jamais|rien|aucun|aucune|personne|ni)(?!\w))", " ", text)
    for term in _NEGATION.get(_lexicon_for(language), ()):
        spans.update(match.span() for match in re.finditer(_phrase_pattern(term), text))
    # A phrase and its constituent marker count as one negation.
    return sum(not any(a <= start and end <= b and (a, b) != (start, end)
                       for a, b in spans) for start, end in spans)


def _numbers(text: str) -> Counter:
    return Counter(_number_value(value) for value in _NUMBER_RE.findall(_numeric_text(text)))


def _number_value(value: str) -> str:
    """Compare exact values, including unreduced fractions and decimals."""
    # Commas remain literal: without locale context they may be separators
    # or decimal marks. Never guess their interpretation.
    if "," in value:
        return value
    try:
        return str(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return value


def _vi_number(words: list[str]) -> int | None:
    """Parse grammatical Vietnamese integers, not arbitrary adjacent digits."""
    digits = dict(zip("không một hai ba bốn năm sáu bảy tám chín".split(), range(10)))
    variants = {"mốt": 1, "tư": 4, "lăm": 5}
    for names, scale in (({"tỷ", "tỉ"}, 10**9), ({"triệu"}, 10**6),
                         ({"nghìn", "ngàn"}, 1000)):
        positions = [i for i, word in enumerate(words) if word in names]
        if positions:
            if len(positions) != 1:
                return None
            index = positions[0]
            head = _vi_number(words[:index])
            tail = _vi_number(words[index + 1:]) if words[index + 1:] else 0
            if head is None or not 0 < head < scale or tail is None or tail >= scale:
                return None
            return head * scale + tail
    words = list(words)
    total = 0
    if len(words) >= 2 and words[0] in digits and words[1] == "trăm":
        total = digits[words[0]] * 100
        words = words[2:]
        if words and words[0] in {"linh", "lẻ"}:
            words = words[1:]
            if len(words) != 1 or words[0] not in digits:
                return None
    if not words:
        return total
    if words[0] == "mười":
        # 'mười năm' is ten years; fifteen is 'mười lăm'.
        if len(words) == 1:
            return total + 10
        if len(words) == 2 and words[1] in {**digits, **variants} and words[1] != "năm":
            return total + 10 + {**digits, **variants}[words[1]]
    elif len(words) >= 2 and words[0] in digits and digits[words[0]] >= 2 and words[1] == "mươi":
        if len(words) == 2:
            return total + digits[words[0]] * 10
        if len(words) == 3 and words[2] in {**digits, **variants} and words[2] != "năm":
            return total + digits[words[0]] * 10 + {**digits, **variants}[words[2]]
    elif len(words) == 1 and words[0] in digits:
        return total + digits[words[0]]
    return None


def _vi_numeric_text(text: str, *, indefinite: bool = False) -> str:
    # Degree/indefinite expressions have no cardinal value. Do not erase
    # 'một' before arbitrary nouns or a following real measurement.
    text = re.sub(r"(?<!\w)(?:một|1)\s+(?:chút|tí|ít|số)(?!\w)", " ", text)
    words = "không một mốt hai ba bốn tư năm lăm sáu bảy tám chín mười mươi trăm linh lẻ nghìn ngàn triệu tỷ tỉ"
    token = "(?:" + "|".join(words.split()) + ")"
    # Measurement aliases and count classifiers distinguish quantities from
    # homographs ('chín chắn', 'tám chuyện') and names ('Anh Hai').
    contexts = {alias for aliases in _UNIT_ALIASES.values() for alias in aliases}
    contexts.update("cái chiếc con người lần phần bộ cuốn quyển tấm máy chai quả ly cốc hộp"
                    " bức bước chương mục tập kỳ trang vòng loạt"
                    # Counted nouns whose classifier Vietnamese spells out.
                    " bài câu chữ từ đoạn điểm ví dụ cách việc hình con số".split())
    context = "(?:" + "|".join(_phrase_pattern(alias) for alias in sorted(contexts, key=len, reverse=True)) + ")"
    # Nouns that take a plain count ('ba câu hỏi'). A leading 'một'/'1' here
    # is the indefinite article ('một câu rất dài'), not a counted object.
    plain_count = "(?:" + "|".join(_phrase_pattern(alias) for alias in sorted(
        "bài câu chữ từ đoạn điểm ví dụ cách việc hình".split(), key=len, reverse=True)) + ")"
    # The model also writes that article with a digit ('1 bài hát'); digits are
    # not number tokens, so strip the digit form here too.
    text = re.sub(r"(?<!\w)1(?=\s+" + plain_count + r")", " ", text)

    def convert(match):
        tokens = match.group().split()
        for length in range(len(tokens), 0, -1):
            prefix = " ".join(tokens[:length])
            value = _vi_number(tokens[:length])
            following = text[match.start() + len(prefix):]
            measured = bool(re.match(r"\s+" + context, following))
            if (length == 1 and length == len(tokens)
                    and tokens[0] in {"một", "1"}
                    and re.match(r"\s+" + plain_count, following)):
                # 'một câu rất dài' is 'a very long sentence' in both C6 and
                # C7, and the model writes that article as '1 câu' too, so
                # both spellings drop the numeral. Only 'ba câu' and above
                # are counted objects.
                return " " + match.group()[len(tokens[0]):].lstrip()
            article = (indefinite and tokens[0] in {"một", "1"} and bool(
                re.match(r"\s+(?!số\b)[^\W\d_]", following)))
            unambiguous = length > 1 or article
            if value is not None and (measured or (length == len(tokens) and unambiguous)):
                return str(value) + match.group()[len(prefix):]
        # A negation next to a quantity ('không một...') is not one integer.
        if len(tokens) > 1:
            return tokens[0] + " " + _vi_numeric_text(" ".join(tokens[1:]), indefinite=indefinite)
        return match.group()

    return re.sub(r"(?<!\w)" + token + r"(?:\s+" + token + r")*(?!\w)", convert, text)


def _numeric_text(text: str) -> str:
    """Comparison-only quantity normalization; never modify approved text.

    Chinese quantities need a following measurement/classifier, and ambiguous
    Vietnamese ba/năm need a quantity context. Names such as 张三 stay intact.
    """
    text = _normalize(text)
    # A numeral inside a personal name is not a quantity (张三 = 'Zhang San').
    # This runs before every numeral rule so no later rule can revive it.
    text = _mask_zh_name_numerals(text)
    # Lexical quantities ('a little', 'some', 'absolutely not') have no value.
    text = _mask_zh_lexical_quantities(text)
    text = re.sub(r"([\d零〇一二三四五六七八九两兩十百千万萬]+)[点點][钟鐘]", r"\1小时", text)
    # 一点点 is a degree expression; 一点五 is the decimal 1.5. Only
    # digit-valued characters after 点 permit decimal normalization.
    text = re.sub(r"(" + _ZH_NUMBER + r")[点點]([零〇一二三四五六七八九]+)",
                  lambda m: _integer_text(m.group(1)) + "." + _integer_text(m.group(2)), text)
    # Chinese puts the denominator first; Vietnamese and slash notation put
    # the numerator first. Normalize the complete fraction before cardinals.
    text = re.sub(r"(?<![\d零〇一二三四五六七八九两兩十百千万萬])百分之\s*(" + _ZH_NUMBER + r"|\d+(?:\.\d+)?)",
                  lambda m: _integer_text(m.group(1)) + "%", text)
    text = re.sub(r"(" + _ZH_NUMBER + r"|\d+)\s*分之\s*(" + _ZH_NUMBER + r"|\d+)",
                  lambda m: _integer_text(m.group(2)) + "/" + _integer_text(m.group(1)), text)
    vi_number = r"(?:\d+|(?:" + _VI_NUMBER_WORDS + r")(?:\s+(?:" + _VI_NUMBER_WORDS + r"))*)"
    text = re.sub(r"(?<!\w)(" + vi_number + r")\s+phần\s+trăm(?!\w)",
                  lambda m: _integer_text(m.group(1)) + "%", text)
    text = re.sub(r"(?<!\w)(" + vi_number + r")\s+phần\s+(" + vi_number + r")(?!\w)",
                  lambda m: _integer_text(m.group(1)) + "/" + _integer_text(m.group(2)), text)
    # Ordinal slots must not also masquerade as cardinal quantities.
    text, _ = _ordinal_text(text, "chinese")
    text, _ = _ordinal_text(text, "vietnamese")
    text, _ = _ordinal_text(text, "english")
    # A superlative numeral ('最正的一個') is an article, not a counted object,
    # so it is removed before any classifier rule can turn it into a number.
    text = re.sub(r"(?<=[最很极極])[^，。！？；\s]{0,4}一[个個]?", " ", text)
    # 一首/一篇/一首歌 is 'a song/article', the indefinite article Vietnamese
    # renders as 'một bài'. Only 二手 and above are counted objects, and an
    # explicit 'only/exactly' marker forces the counted reading.
    text = re.sub(r"(?<!只有)(?<!一共)(?<!恰好)(?<!正好)(?<!零)(?<![零〇二两兩三四五六七八九十百千万萬])"
                  r"一(?:首|篇|首歌|篇兒|本首)", " ", text)
    text = re.sub(r"(?<![\d零〇一二三四五六七八九两兩十百千万萬])" + _ZH_NUMBER + r"?(?=\s*(?:岁|歲|歳|系列|期|章|节|節|集|步|页|頁|轮|輪|年|月|周|週|个|個|台|只|瓶|本|支|件|位|名|条|條|张|張|次|份|杯|碗|套|座|架|盒|枚|颗|顆|棵|辆|輛|篇|根|隻|只|指|首|度|公斤|千克|克|公里|千米|米|升|秒|分钟|分鐘|小時|小时|天|元|块|塊|%))", lambda m: _integer_text(m.group()), text)
    # 兩眼 (two eyes), 兩手 (both hands), 三貓 (three cats): a numeral of 2 or
    # more followed by any noun is a counted quantity. Chinese has no measure
    # word here, so a classifier inventory would miss ordinary counts and reject
    # correct translations. 一 + noun stays an article: it is handled above.
    text = re.sub(r"(?<![\d零〇一二三四五六七八九十百千万萬])(?<![第每])(" + _ZH_NUMBER + r")(?=" + _ZH_COUNT_NOUN + r")",
                  lambda m: _integer_text(m.group(1)), text)
    # A bare classifier with no numeral ('a girl', 'a root of finger') is an
    # indefinite article, which Vietnamese renders as 'một'. Count it so both
    # sides agree instead of reporting the target's article as a lost number.
    # 个 that already belongs to a duration alias (個月/個星期) is a unit, not
    # an article, so it must not be turned into a count. Only the unit-forming
    # collisions are excluded: 個月 reads as 'a month', while a bare 個 in front
    # of an ordinary noun — including 月亮 (the moon) — is an article, and
    # treating it as a month unit made a correct translation look invented.
    text = re.sub(r"(?<![\d零〇一二三四五六七八九两兩十百千万萬])(?<![各每這这那哪兒那個这个這個幾几數数好])(个|個)(?!月(?![亮])|周|週|星期|天|年|秒|分|刻|小時|时)", r"1\1", text)
    # A 第N ordinal has already been extracted as an ordinal slot by the caller,
    # leaving its bare classifier (第二个视频 -> <ordinal> 个视频). The rule
    # above turns that classifier into "1个"; both spellings are the ordinal's
    # own article, not an extra counted object, so the added 1 must be removed
    # again. Without this a correct 'Video thứ hai' looks like a lost count.
    text = re.sub(r"(?<=" + _ORDINAL_MARKER + r"\s)1(?=个|個)", " ", text)
    # 首 in 首歌/詩歌 is a lexical compound, not a bare classifier.
    text = re.sub(r"(?<![\d零〇一二三四五六七八九两兩十百千万萬])(?<![各每這这那哪兒那個这个這個幾几數数好])(?<![歌詩诗詞词樂乐])"
                  r"([隻根])", r"1\1", text)
    text = re.sub(r"(?<![\d零〇一二三四五六七八九两兩十百千万萬])(?<![各每這这那哪兒那個这个這個幾几數数好])(?<![歌詩诗詞词樂乐])首", "1首", text)
    # A numeral standing alone in a spoken clause is a counted value even
    # without a classifier (人中一 下巴二). Lexicalised compounds such as
    # 一起/一下 are not followed by a clause boundary and stay untouched.
    text = re.sub(r"(?<![第每])(?<=[\u3400-\u9fff])(" + _ZH_NUMBER + r")(?=\s|$)", lambda m: _integer_text(m.group()), text)
    return _vi_numeric_text(text)


_ZH_NUMBER = r"[零〇一二三四五六七八九两兩十百千万萬]+"
# A numeral of two or more is a counted quantity whatever noun follows it
# (兩眼 = two eyes, 三天 handled above). Chinese has no separate classifier
# inventory for body parts or common nouns, so enumerating measure words
# misses ordinary counts; restricting this rule to 2+ cannot collide with the
# 一 article readings handled above.
_ZH_COUNT_NOUN = r"[\u3400-\u9fff\uf900-\ufaff]"

# Chinese has no spaces, so 张三 ('Zhang San') and 张三瓶 ('three bottles') are
# written identically far enough for a numeral scanner. The discriminator is
# the surname inventory: a single numeral directly after a surname is part of a
# personal name, unless a measure/classifier/unit word follows it, in which case
# the counted reading is the only one that makes sense (王五岁 = Wang is five,
# 老王五元 = five yuan). Requiring that exception is what keeps genuine counts
# (每隔三天, 买了三瓶, 三瓶水) counted instead of masking them away. The list is
# the standard common-surname inventory (simplified and traditional spellings
# side by side), not a fixture-specific whitelist: any unlisted surname falls
# back to the counted reading, which is the blocking direction.
_ZH_SURNAMES = "".join(dict.fromkeys(
    "".join(pair) for pair in (
        "赵趙", "钱錢", "孙孫", "李", "周", "吴吳", "郑鄭", "王", "冯馮", "陈陳",
        "褚", "卫衛", "蒋蔣", "沈", "韩韓", "杨楊", "朱", "秦", "尤", "许許",
        "何", "吕呂", "施", "张張", "孔", "曹", "严嚴", "华華", "金", "魏",
        "陶", "姜", "戚", "谢謝", "邹鄒", "喻", "柏", "水", "窦竇", "章",
        "云雲", "苏蘇", "潘", "葛", "奚", "范", "彭", "郎", "鲁魯", "韦韋",
        "昌", "马馬", "苗", "凤鳳", "花", "方", "俞", "任", "袁", "柳",
        "酆", "鲍鮑", "史", "唐", "费費", "廉", "岑", "薛", "雷", "贺賀",
        "倪", "汤湯", "滕", "殷", "罗羅", "毕畢", "郝", "邬鄔", "安", "常",
        "乐樂", "于", "时時", "傅", "皮", "卞", "齐齊", "康", "伍", "余",
        "元", "卜", "顾顧", "孟", "平", "黄黃", "和", "穆", "萧蕭", "尹",
        "姚", "邵", "湛", "汪", "祁", "毛", "禹", "狄", "米", "贝貝",
        "明", "臧", "计計", "伏", "成", "戴", "谈談", "宋", "茅", "庞龐",
        "熊", "纪紀", "舒", "屈", "项項", "祝", "董", "梁", "杜", "阮",
        "蓝藍", "闵閔", "席", "季", "麻", "强強", "贾賈", "路", "娄婁",
        "危", "江", "童", "颜顏", "郭", "梅", "盛", "林", "刁", "钟鐘",
        "徐", "邱", "骆駱", "高", "夏", "蔡", "田", "樊", "胡", "凌",
        "霍", "虞", "万萬", "支", "柯", "昝", "管", "卢盧", "莫", "经經",
        "房", "裘", "缪", "干", "解", "应應", "宗", "丁", "宣", "贲賁",
        "邓鄧", "郁", "单單", "杭", "洪", "包", "诸諸", "左", "石", "崔",
        "吉", "钮鈕", "龚龔", "程", "嵇", "邢", "滑", "裴", "陆陸",
        "荣榮", "翁", "荀", "羊", "甄", "曲", "家", "封", "芮", "储儲",
        "靳", "汲", "邴", "糜", "松", "井", "段", "富", "巫", "乌烏",
        "焦", "巴", "弓", "牧", "隗", "山", "谷", "车車", "侯", "宓",
        "蓬", "全", "郗", "班", "仰", "秋", "仲", "伊", "宫宮", "宁寧",
        "仇", "栾欒", "暴", "甘", "钭鈄", "厉厲", "戎", "祖", "武", "符",
        "刘劉", "景", "詹", "束", "龙龍", "叶葉", "幸", "司", "韶", "郜",
        "黎", "蓟", "薄", "印", "宿", "白", "怀懷", "蒲", "邰", "从從",
        "鄂", "索", "咸", "籍", "赖賴", "卓", "蔺藺", "屠", "蒙", "池",
        "乔喬", "阴陰", "鬱", "胥", "能", "苍蒼", "双雙", "闻聞", "莘",
        "党黨", "翟", "谭譚", "贡貢", "劳勞", "姬", "申", "扶", "堵",
        "冉", "宰", "郦酈", "雍", "郤", "璩", "桑", "桂", "濮", "牛",
        "寿壽", "通", "边邊", "扈", "燕", "冀", "郏", "浦", "尚", "农農",
        "温溫", "别別", "庄莊", "晏", "柴", "瞿", "阎閻", "充", "慕",
        "连連", "茹", "习習", "宦", "艾", "鱼魚", "容", "向", "古", "易",
        "慎", "戈", "廖", "庾", "终終", "暨", "居", "衡", "步", "都",
        "耿", "满滿", "弘", "匡", "国國", "文", "寇", "广廣", "禄祿",
        "阙闕", "东東", "欧歐", "殳", "沃", "利", "蔚", "越", "夔",
        "隆", "师師", "巩鞏", "厍", "聂聶", "晁", "勾", "敖", "融", "冷",
        "訾", "辛", "阚闞", "那", "简簡", "饶饒", "空", "曾", "毋", "沙",
        "乜", "养養", "鞠", "须須", "丰豐", "巢", "关關", "蒯", "相",
        "查", "后後", "荆荊", "红紅", "游", "竺", "权權", "逯", "盖蓋",
        "益", "桓", "公",
    )))
# A following measure word rules out the name reading. This is the union of the
# classifier inventory already used above plus the units the guard measures, so
# a counted reading is never masked.
_ZH_MEASURE_SUFFIX = "".join(dict.fromkeys(
    ("个個次期章节節集步页頁轮輪年台只瓶本支件位名条條张張份杯碗套座架盒枚"
     "颗顆棵辆輛篇根隻指首度双雙对對副串堆群束排段幅袋罐盆盘盤碟顶頂册冊"
     "季幕场場层層部匹头頭尾枝株间間家组組桶勺升毫克厘千公里米元塊块点點"
     "号號款項项通岁歲日元角毛寸斤周週天时分秒公斤千克")
))
_ZH_NAME_MARKER = "\ue001"
# Lexical quantities carry no countable value, so a scanner that treats their
# numeral as a count invents a fact the speaker never made. Each entry is a
# fixed Chinese expression, and every one of them has a Vietnamese rendering
# that is also not a number ('một chút'/'1 chút' is already erased as a degree
# expression, 'vài' is never a numeral).
_LEXICAL_QUANTITIES = (
    # 'a little' — 有點/有点, 一點/一点, 一點兒. 一点五 (1.5) is a decimal and is
    # excluded by the lookahead so the decimal rule still owns it. 一点点 is the
    # reduplicated degree form ('just a little bit more'); the trailing 点 is
    # not a decimal digit, so the negative lookahead must allow it, otherwise
    # 再高一点点 reads as the count '1' and a correct translation is rejected.
    r"(?:有|沒|没|不)?一[点點](?:[点點]|儿|兒)?(?![点點零〇一二三四五六七八九\d])",
    # 'some / a few' — 一些, 一會兒/一会儿, 一陣子, 一會.
        r"一(?=[些會会陣阵])",
        # '一下 / 一下子 / 下子' is the verb-reducing particle ('have a look',
        # 'wait a moment'): like 一點 it is NOT a count, and its Vietnamese
        # rendering ('một chút', or nothing at all) legitimately has no numeral.
        # Only the 一 directly before 下 is masked, so a real count in the same cue
        # (一下买了三个) is still compared as a count.
        r"一(?=下(?:子|子儿|子兒)?)",
    # '五官' is a fixed compound ('the five features': eyes, nose, mouth,
    # ears, brows) used for a FACE, never a count of five items. Its
    # Vietnamese rendering ('ngũ quan', 'đường nét khuôn mặt') carries no
    # numeral, so counting the 五 rejects a correct translation.
    r"五官",
    # 一劳永逸 / 一勞永逸 is the fixed idiom 'once and for all'. Its 一 names
    # the single effort, not one counted object, so the natural Vietnamese
    # ('giải quyết triệt để') legitimately has no numeral. Masking the whole
    # idiom keeps the 一 from being read as a cardinal.
        r"一[劳勞]永逸",
    # 星期一..星期六/日 is a WEEKDAY, whose numeral is a rank inside the word,
    # not a counted object. Vietnamese renders it as 'thứ hai'..'thứ bảy' and
    # carries no cardinal at all. 星期天/日 stays out because it has no numeral.
        r"星期[零〇一二三四五六七八九十]",
    # 'absolutely / never' — 千萬 is a verb of emphasis before a prohibition
    # (千萬不能 = 'must absolutely not'), not 10,000,000. A numeric prefix
    # (一千萬) or a non-prohibition reading keeps the counted meaning.
        r"(?<![零〇一二三四五六七八九两兩十百千万萬\d])千萬(?=\s*(?:不能|不可|不要|不許|不许|別|别|勿|切記))",
)
_ZH_LEXICAL_QUANTITY = re.compile("|".join(_LEXICAL_QUANTITIES))


def _mask_zh_lexical_quantities(text: str) -> str:
    """Blank the numeral of a lexical (degree / indefinite) quantity.

    This runs before every numeral rule, so '剃掉一點' and '千萬不能' no
    longer contribute a count that the translation was right not to reproduce.
    The expressions are fixed strings, not cue-specific patterns, so a new cue
    containing the same words is fixed by the same rule.
    """
    return _ZH_LEXICAL_QUANTITY.sub(_ZH_NAME_MARKER, text)


def _mask_zh_name_numerals(text: str) -> str:
    """Blank out numerals that are part of a personal name.

    Without this, 张三 becomes 张3 and a correct translation ('Trương Tam đã
    đến') is rejected for inventing or losing a count that was never claimed.
    A numeral is only masked when the character immediately before it is a
    known surname and no measure word follows it, so every genuine count keeps
    its value and an unlisted surname stays on the counted (blocking) side.
    """
    surnames = "[" + re.escape(_ZH_SURNAMES) + "]"
    numeral = _ZH_NUMBER[:-1]  # the character class without its "+" quantifier
    measure = "[" + re.escape(_ZH_MEASURE_SUFFIX) + "]"
    # A multi-character numeral (张三十 / 三十) is never a bare given name, and
    # a following measure word means the counted reading (王五岁). Both must
    # fall through to the ordinary counted rules.
    pattern = surnames + "(" + numeral + ")(?!" + numeral + r")(?!" + measure + ")"
    def mask(match):
        prefix, suffix = text[:match.start()], text[match.end():]
        # A surname-shaped character INSIDE a noun is not a subject name:
        # 下巴二 labels the chin '2'; it must not become the person 巴二.
        embedded = bool(prefix and re.search(r"[\u3400-\u9fff]$", prefix))
        introduced = bool(re.search(r"(?:老|小|阿|叫|和|与|與|找|问|問|说|說)$", prefix))
        predicate = bool(re.match(r"(?:来|來|说|說|问|問|买|買|今年|带|帶|走|去|在|要|不|没|沒)", suffix))
        if embedded and not introduced and not predicate:
            return match.group()
        return match.group()[0] + _ZH_NAME_MARKER
    return re.sub(pattern, mask, text)

def _elided_age_pattern():
    global _ZH_ELIDED_AGE
    if _ZH_ELIDED_AGE is None:
        _ZH_ELIDED_AGE = re.compile(
            r"^(?:我|你|他|她|咱)\s*(" + _ZH_NUMBER + r"|\d+)\s*了(?:\s|$|[，。！？])")
    return _ZH_ELIDED_AGE
# Vietnamese unit words that are ordinary words in ordinary sentences:
# 'bao giờ' (never) is not an hour count, 'bao nhiêu tuổi' (how old) is not
# an age measurement and 'đồng tử' (pupil) is not currency. They only name a
# unit when a quantity is actually present.
_AMBIGUOUS_UNITS = frozenset({"đồng", "năm", "gam", "độ", "day", "giờ", "tuổi"})
_VI_NUMBER_WORDS = "không|một|mốt|hai|ba|bốn|tư|năm|lăm|sáu|bảy|tám|chín|mười|mươi|trăm|linh|lẻ|nghìn|ngàn|triệu|tỷ|tỉ"
_ZH_CLASSIFIER = r"(?:个|個|次|期|章|节|節|集|步|页|頁|轮|輪|年|月|天|周|週|台|只|瓶|本|支|件|位|名|条|條|张|張|份|杯|碗|套|座|架|盒|枚|颗|顆|棵|辆|輛|篇)"
_ORDINAL_MARKER = "\ue000"


def _integer_text(token: str) -> str:
    if re.fullmatch(r"\d+(?:\.\d+)?", token):
        return token
    if not re.fullmatch(_ZH_NUMBER, token):
        if token in {"tư", "mốt", "lăm", "nhất", "đầu tiên"}:
            return str({"tư": 4, "mốt": 1, "lăm": 5, "nhất": 1, "đầu tiên": 1}[token])
        value = _vi_number(token.split())
        return str(value) if value is not None else token
    digits = {char: value for value, char in enumerate("零一二三四五六七八九")}
    digits.update({"〇": 0, "两": 2, "兩": 2})
    scales = {"十": 10, "百": 100, "千": 1000, "万": 10000, "萬": 10000}
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


def _ordinal_text(text: str, language: str | None, *, standalone: bool = True, labelled: bool = False) -> tuple[str, list[str]]:
    """Extract ordinal slots separately from cardinal values, for comparison."""
    text = _normalize(text)
    ordinals = []

    def slot(match):
        token = match.group(1)
        english = {"first": "1", "second": "2", "third": "3", "fourth": "4",
                   "fifth": "5", "sixth": "6", "seventh": "7", "eighth": "8",
                   "ninth": "9", "tenth": "10"}
        value = (english.get(token, re.sub(r"(?<=\d)(?:st|nd|rd|th)$", "", _integer_text(token)))
                 if token else "1" if match.group().endswith("đầu tiên") else "next")
        # Keep the unit's ordinal context, without counting the rank as a
        # quantity. Vietnamese 'ngày sau' includes the unit in the match.
        time_unit = re.match(r"(?:năm|tháng|ngày|tuần)\b", match.group())
        for unit in ("day", "week", "month", "year"):
            if any(re.match(r"\s*" + _phrase_pattern(alias), text[match.end():])
                   or re.search(_phrase_pattern(alias) + r"\s*$", text[:match.start()])
                   or (time_unit and time_unit.group() == alias)
                   for alias in _UNIT_ALIASES[unit]):
                value += ":" + unit
                break
        ordinals.append(value)
        return " " + (time_unit.group() + " " if time_unit else "") + _ORDINAL_MARKER + " "

    if _lexicon_for(language) == "chinese":
        pattern = (r"第\s*(" + _ZH_NUMBER + r"|\d+)(?=\s*(?:" + _ZH_CLASSIFIER + r"|$|[，。！？；、]|\s))"
                   r"|下\s*(?:一\s*(?=" + _ZH_CLASSIFIER + r")|(?=[个個次期章节節集步页頁轮輪年月天周週]))|接下[来來]")
    elif _lexicon_for(language) == "vietnamese":
        number = r"(?:nhất|\d+|(?:" + _VI_NUMBER_WORDS + r")(?:\s+(?:" + _VI_NUMBER_WORDS + r"))*)"
        # A standalone 'thứ hai' can be Monday. Require rank context unless
        # the source already has an ordinal slot; never guess a calendar rank.
        context = "" if standalone else r"(?:video|lần|kỳ|chương|tập|bước|trang|vòng|mục|phần|đoạn|bài|người|chiếc|cái)\s+"
        # A numbered section ('Bước 4') can render an explicit source rank
        # ('第四'). Licence this ONLY at the cross-language comparison, not
        # globally in cardinal normalization or '4 bước' measurements.
        rank = r"(?:thứ|bước|phần|mục|chương|tập|đoạn|bài|trang|vòng)" if labelled else "thứ"
        pattern = (r"(?<!\w)" + context + r"(?:" + rank + r"\s+(" + number + r")|đầu tiên)(?!\w)"
                   r"|(?<!\w)" + context + r"(?:tiếp theo|kế tiếp|kế đó|tiếp đến|sau đó)(?!\w)"
                   r"|(?<!\w)(?:lần|kỳ|chương|tập|bước|trang|vòng|năm|tháng|ngày|tuần)\s+sau(?!\w)")
    elif _lexicon_for(language) == "english":
        # 'second' alone can be a unit; 'first aid' is lexical. Require a
        # ranked/sequential noun rather than suppressing these words globally.
        noun = r"(?:steps?|chapters?|sections?|episodes?|pages?|rounds?|times?|days?|weeks?|months?|years?|videos?|items?|parts?|stages?|attempts?)"
        pattern = (r"(?<!\w)(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th))"
                   r"(?=\s+" + noun + r"\b)|(?<!\w)next(?=\s+" + noun + r"\b)")
    else:
        return text, ordinals
    # A list preserves ordinal order: swapping chapter 2/section 3 must fail.
    return re.sub(pattern, slot, text), ordinals


def _unit_spans(text: str) -> list[tuple[int, int, str]]:
    text = _numeric_text(text)
    spans = set()
    for unit, aliases in _UNIT_ALIASES.items():
        for alias in aliases:
            # SI suffixes can be attached to a number (5kg); single letters
            # are units only in an explicit numeric measurement.
            if re.fullmatch(r"[a-z]+", alias):
                for match in re.finditer(r"\d\s*(" + re.escape(alias) + r")(?![^\W\d_])", text):
                    spans.add((*match.span(1), unit))
                if len(alias) == 1:
                    continue
            for match in re.finditer(_phrase_pattern(alias), text):
                # Names such as Ngô Tiểu Đồng / 马克 and ordinary words such
                # as "năm" are not currency/grams/years without a quantity.
                if _SCRIPT_RE.search(alias) or alias in _AMBIGUOUS_UNITS:
                    before = re.search(r"\d\s*$", text[:match.start()])
                    if alias == "đồng" and re.match(r"\s*tử", text[match.end():]):
                        # đồng tử is the pupil, not currency.
                        continue
                    # 月亮 is the moon, not a month count. This holds for the
                    # 个月/個月 spellings too: 1個月亮下巴 is
                    # 'a moon-shaped chin', not 'a one-month chin'.
                    if unit == "month" and re.match(r"\s*[亮亮]", text[match.end():]):
                        continue
                    ordinal_unit = unit in {"day", "week", "month", "year"} and (
                        re.search(_ORDINAL_MARKER + r"\s*$", text[:match.start()])
                        or re.match(r"\s*" + _ORDINAL_MARKER, text[match.end():]))
                    after = re.match(r"\s*\d", text[match.end():]) if (
                        unit in {"usd", "eur", "vnd", "cny", "percent"} and alias not in {"đồng", "元", "块", "塊"}) else None
                    if not before and not after and not ordinal_unit:
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
            measurements[(_number_value(before.group(1)), unit)] += 1
        elif unit in {"percent", "usd", "eur", "vnd"}:
            # Currency/percentage markers can precede or follow the value.
            after = re.match(r"\s*(" + _NUMBER_RE.pattern + r")", text[end:])
            if after:
                measurements[(_number_value(after.group(1)), unit)] += 1
    return measurements


def _polarity_hits(text: str, first: str, second: str) -> set[str]:
    hits = set()
    for term, other in ((first, second), (second, first)):
        # 'muốn' inside 'không muốn' is not an affirmative assertion.
        scrubbed = re.sub(_phrase_pattern(other), " ", _normalize(text)) if term in other else text
        if _contains(scrubbed, term):
            hits.add(term)
    return hits


def _translation_question(text: str, language: str | None) -> bool:
    text = _normalize(text)
    if _lexicon_for(language) == "vietnamese":
        _, interrogative = _vi_question_text(text)
        # WH-location questions need no final particle or ASR punctuation.
        # Do not turn negative 'không đi đâu' or 'bất kỳ nơi nào' into asks.
        for match in re.finditer(
            r"(?<!\w)(?:(?:ở|từ|đến|đi|ra|về|qua|bên|phía)\s+đâu|"
            r"(?:nơi|chỗ|vùng|miền|nước|tỉnh|thành phố)\s+nào)(?!\w)", text
        ):
            prefix = re.split(r"[.!?;,]", text[:match.start()])[-1]
            if re.search(r"(?:không|chẳng|chả|chưa|đừng|bất kỳ|bất cứ)\s*$", prefix):
                continue
            # 'I know WHERE you live' is an assertion, not an ask. Explicit
            # final particles still count, and 'tell me where' stays a request.
            reported = re.search(r"(?<!\w)(?:biết|nhớ|rõ)(?!\w)", prefix)
            requested = re.search(r"(?:hãy|muốn|cho\s+(?:tôi|mình))\s+(?:biết|nhớ|rõ)(?!\w)", prefix)
            if not reported or requested:
                interrogative = True
        return "?" in text or interrogative
    return "?" in text or (_lexicon_for(language) == "chinese" and bool(
           # A cue that holds only the interrogative predicate (哪来的) has no 吗,
           # no final punctuation and no clause-final wh-word, so every existing
           # branch misses it. 哪来 is a complete interrogative on its own.
           _ZH_WHENCE.search(text)
           or re.search(r"[吗嗎](?=\s*(?:[。，,;；。.!?！]|[嗯啊呃]|$))", text)
       # Spoken ASR often separates a cue at the clause boundary instead of
       # punctuation: 喜欢吗 喜欢啊. A 吗 closing any spoken clause is
       # interrogative; 吗啡 (morphine) is not, because 啡 is not a boundary.
       or re.search(r"[吗嗎](?=\s|[。，,;；。.!?！]|$)", text)
       # A-not-A alternation (有没有 / 喜不喜欢) and the tag/discourse
       # particles 吧 / 对吧 / 好不好 are questions without an 吗. Spoken ASR
       # rarely keeps the closing punctuation the 吗 rule depends on.
       or re.search(r"有没有|有沒有|喜不喜欢|喜不喜歡|好不好|是不是|能不能|可不可以"
                    r"|会不会|會不會|要不要|行不行|对不对|對不對", text)
       # Tag questions are anchored at the end of the cue.
       or re.search(r"(?:对吧|對吧|好吧|是吗|对吗|對嗎|行吧|可以吧|对不对|對不對)(?=$|[\s，,。.!?！])", text)
       # A clause-final interrogative word is a question in itself. Spoken ASR
       # drops the closing punctuation the 吗 rule would otherwise need.
       or re.search(r"(?:哪里|哪裡|哪儿|哪兒|哪|怎么|怎麼|为什么|為什麼|多少|几点|幾點|谁|誰|何时|何時)(?=$|[\s，,。.!?！])", text)
        # ASR often omits the comma after a leading discourse question.
        # Require a question verb and a following subject, not any internal
        # 吗 (for example 吗啡, morphine).
        or re.search(r"(?:^|[。，,;；.!?！])(?:你(?:们|們)?不?|不)?(?:知道|明白|懂)[吗嗎](?=\s*[我你他她它这這那])", text)))


def _vi_question_text(text: str) -> tuple[str, bool]:
    """Remove only grammatical final particles, independently of source intent."""
    particles = 0

    def strip_particle(match):
        nonlocal particles
        prefix = match.group("prefix")
        # Bare answers and reported negatives are not interrogative particles.
        reported = re.search(r"(?:là|trả lời|đáp án|nói|đáp|bảo)\s*$", prefix)
        modal = re.search(r"(?<!\w)có(?:\s+thể)?\s+", prefix)
        if not re.search(r"[^\W\d_]", prefix) or (reported and not modal):
            return match.group()
        # A particle only closes the SENTENCE when everything after it is
        # terminal punctuation. '... cua ban a, ban xinh qua' uses the particle
        # to mark realization inside a running sentence, which is not a
        # question; the same particle at the end of the target still is one.
        tail = match.string[match.end():]
        clause_final = not re.search(r"[^\W\d_]", re.sub(r"[\s?!.,;:]+$", "", tail))
        if clause_final:
            particles += 1
        elif re.search(r"(?<!\w)(?:biết|hiểu|nghe|thấy|được|sẵn sàng|đồng ý)(?!\w)\s*$", prefix):
            # A comprehension/readiness question that a cue boundary splits in
            # half: 你知道吗，继续看 -> 'Bạn biết không, xem tiếp nhé'. The
            # Vietnamese 'không' tag sits before a comma instead of closing the
            # cue, exactly like the Chinese 吗 the source rule already accepts.
            # Only a comprehension/readiness predicate is licensed, so a genuine
            # negation followed by another clause stays a negation.
            particles += 1
        return prefix + " "

    # The lookahead limits matching to a particle that already closes a clause
    # (comma / terminator / end). This keeps the negation-scope rewriting below
    # behaving exactly as before; only the COUNT above is clause-sensitive.
    pattern = (r"(?P<prefix>[^?.!,;]+?)\s+(?:không|chưa|chứ|hả|hở|nhỉ|ư|à)"
               r"(?:\s+(?:ạ|nhỉ|vậy))?(?=\s*(?:[?.,!;]|$))")
    text = re.sub(pattern, strip_particle, _normalize(text))
    return text, bool(particles)


def _article_tokens(text: str, language: str | None) -> list[str]:
    """Numeral occurrences that read as an indefinite article, not a count.

    'tự chụp 1 tấm ảnh' counts a photo (tấm is a classifier); '1 khuôn mặt' and
    '1 cái cằm' name a thing without counting anything. Same numeral, different
    role, and the role decides whether an added or missing 1 is a fact change.
    Returns the numeric values that are articles, so the caller can subtract
    them from the plain count comparison instead of dropping the check.
    """
    text = _normalize(text)
    articles: list[str] = []
    if _lexicon_for(language) == "chinese":
        # 一个/一個 and a bare 个/個 are the indefinite article. The normalizer
        # has already rewritten a bare 个 to 1个, so match the numeral form too.
        # A bare 个 names a thing without counting it (你是個月亮下巴 = 'you are
        # a moon-shaped chin'), exactly like 一个. Requiring the numeral here
        # made every such cue invent a count the speaker never claimed.
        articles.extend("1" for _ in re.finditer(
            r"(?<![\d零〇一二三四五六七八九十百千万萬])(?:(?:1|[一])?)(?:个|個|首|篇|本|张|張|只|隻|条|條)(?=" + _ZH_COUNT_NOUN + r")",
            text))
        return articles
    if _lexicon_for(language) != "vietnamese":
        return articles
    classifiers = {alias for aliases in _UNIT_ALIASES.values() for alias in aliases}
    classifiers.update("cái chiếc con người lần phần bộ cuốn quyển tấm máy chai quả ly cốc hộp"
                      " bức bước chương mục tập kỳ trang vòng loạt"
                      " bài câu chữ từ đoạn điểm ví dụ cách việc hình con số".split())
    context = "(?:" + "|".join(_phrase_pattern(alias)
                              for alias in sorted(classifiers, key=len, reverse=True)) + ")"
    # The measured reading wins: 1 tấm ảnh is a count even though 1 is present.
    # Only a numeral whose following word is NOT a classifier/measure word and
    # is not preceded by an explicit determiner is an indefinite article.
    for match in re.finditer(r"(?<!\w)(?:một|1|mốt)(?!\s*(?:" + context + r"|\d))(?=\s+[^\W\d_])", text):
        prefix = text[:match.start()].rstrip()
        if re.search(r"(?:chỉ|chính|đúng|cả|chính xác|duy nhất)$", prefix):
            continue
        articles.append("1")
    return articles


def _quantity_roles(text: str, language: str | None) -> Counter:
    """Count (value, role) pairs so article and count readings stay distinct."""
    roles: Counter = Counter()
    articles = _article_tokens(text, language)
    article_values = Counter(_number_value("1") for _ in articles)
    measured = Counter(_number_value(value)
                       for value in _NUMBER_RE.findall(_numeric_text(text)))
    for value, count in measured.items():
        remaining = count - article_values.get(value, 0)
        for _ in range(max(remaining, 0)):
            roles[(value, ROLE_CARDINAL)] += 1
        for _ in range(min(count, article_values.get(value, 0))):
            roles[(value, ROLE_ARTICLE)] += 1
        if remaining < 0:
            roles[(value, ROLE_ARTICLE)] += 0
    return roles


def _implied_age(context: GuardContext | None, source: str,
                 source_language: str | None) -> str | None:
    """The elided age value in `source`, licensed only by a matching question.

    我23了 states an age with no unit word. Accepting the translated unit is
    legitimate ONLY when a neighbouring cue asks for that age: without the
    question the cue could equally be a price, a size or a rank, and a
    translated unit would then be an invented fact.
    """
    if context is None or _lexicon_for(source_language) != "chinese":
        return None
    match = _elided_age_pattern().search(_normalize(source))
    if not match:
        return None
    # The question must exist in the SOURCE window only. A question found in
    # the target window would let the translation license its own invented
    # fact: the target is the text under test, so 'Bao nhiêu tuổi?' appearing
    # there could be exactly the invention the guard exists to catch. An
    # untranslated-source cue is still checkable because the question has to
    # come from the approved side.
    window = _normalize(context.source_window())
    if not (_AGE_QUESTION_ZH.search(window) or _AGE_QUESTION_VI.search(window)):
        return None
    return _integer_text(match.group(1))


def _clause_polarity(text: str, language: str | None) -> list[int]:
    """Negation presence per clause, in order, so scope is not just a count."""
    clauses = [clause for clause in re.split(r"[，,。.！!？?；;]", _normalize(text)) if clause.strip()]
    return [_negations(clause, language) for clause in clauses]


def _invariants(before: str, after: str, source_language: str | None,
                target_language: str | None, *, translation: bool = False,
                context: GuardContext | None = None) -> list[dict]:
    issues = []
    old_question = "?" in _normalize(before)
    new_question = "?" in _normalize(after)
    negation_after = after
    negation_before = before
    numeric_before, numeric_after = before, after
    if translation:
        old_question = _translation_question(before, source_language)
        new_question = _translation_question(after, target_language)
        if _lexicon_for(source_language) == "vietnamese":
            negation_before, _ = _vi_question_text(before)
        if _lexicon_for(target_language) == "vietnamese":
            negation_after, _ = _vi_question_text(after)
        if _lexicon_for(source_language) == "chinese" and _lexicon_for(target_language) == "vietnamese":
            # 不同 is a lexical difference predicate, naturally rendered as
            # 'khác' or 'không giống'. Mask the paired realization only; an
            # outer 不是/không still carries its own negation. 不同意 is disagree.
            different = r"不同(?!意)"
            count = len(re.findall(different, _normalize(negation_before)))
            if count:
                paired_target, matches = re.subn(
                    r"(?<!\w)(?:khác(?:\s+nhau)?|không\s+giống(?:\s+nhau)?)(?!\w)",
                    " ", _normalize(negation_after), count=count)
                if matches == count:
                    negation_before = re.sub(different, " ", _normalize(negation_before))
                    negation_after = paired_target
            # 准 can mean permission or accuracy. Only explicit adjectival /
            # measurement contexts use the 'inaccurate' reading; bare 不准
            # stays a prohibition. Vietnamese may express it lexically as sai.
            inaccurate = (r"不[准準][确確]|(?:测|測|算|猜|预测|預測|估计|估計|判断|判斷)"
                          r"(?:得)?不[准準]|(?:手表|手錶|时钟|時鐘|钟表|鐘錶|表|錶|温度|溫度|读数|讀數)"
                          r"(?:很|太|有点|有點)?不[准準](?=$|[\s，,。.!?！？；;了啊呀呢吧])")
            negation_before, inaccuracies = re.subn(inaccurate, " ", negation_before)
            if inaccuracies:
                negation_after, equivalents = re.subn(
                    r"(?<!\w)(?:không\s+(?:chính xác|chuẩn)|thiếu\s+chính xác|sai)(?!\w)",
                    " ", _normalize(negation_after), count=inaccuracies)
                if equivalents != inaccuracies:
                    issues.append({"kind": "negation_lost", "detail": "inaccuracy predicate lost"})
            if re.search(r"不知不[觉覺]", before):
                # 'Unconsciously' also has a natural negative lexical form.
                # Remove that complete expression, not an unrelated không.
                negation_after = re.sub(
                    r"(?<!\w)(?:lúc nào\s+(?:không|chẳng)\s+hay|(?:không(?:\s+hề)?|chẳng)\s+hay biết)(?!\w)",
                    " ", _normalize(negation_after), count=1)
            pervasive_count = len(re.findall(r"[无無]孔不入", before))
            if pervasive_count:
                # 'No place where it cannot enter' is the positive idiom,
                # not two extra proposition-level negatives. Remove only
                # that complete universal construction; outer negatives stay.
                negation_after = re.sub(
                    r"(?<!\w)(?:không|chẳng)\s+(?:(?:chỗ|nơi)\s+nào|đâu)\s+(?:mà\s+)?(?:không|chẳng)(?!\w)",
                    " ", _normalize(negation_after), count=pervasive_count)
        if _lexicon_for(source_language) == "chinese":
            # 每一个 means each/every, not a cardinal quantity of one.
            numeric_before, distributive = re.subn(r"每\s*一\s*([个個])", r"每\1", _normalize(before))
            if re.search(r"一[劳勞]永逸", numeric_before) and re.search(r"dứt điểm|triệt để|mãi mãi|cho xong|một lần (?:là )?xong", _normalize(after)):
                # The idiom is once-for-all, not one counted object. Only its
                # Vietnamese lexical 'một lần' slot is removed, not other counts.
                numeric_after = re.sub(r"(?<!\w)một\s+lần(?!\w)", " ", _normalize(after), count=1)
            # 一个一个地 is 'one by one', not two counted objects.
            numeric_before, sequential = re.subn(r"一\s*[个個]\s*一\s*[个個](?:地)?", " ", numeric_before)
            if distributive and _lexicon_for(target_language) == "vietnamese":
                if not any(_contains(after, word) for word in ("mỗi", "từng", "mọi")):
                    issues.append({"kind": "quantity_lost", "detail": "each/every quantifier lost"})
                numeric_after = re.sub(r"(?<!\w)((?:mỗi|từng)\s+)một(?!\w)", r"\1", _normalize(after))
            if sequential and _lexicon_for(target_language) == "vietnamese":
                if not any(_contains(after, word) for word in ("từng", "mỗi", "lần lượt", "một một")):
                    issues.append({"kind": "quantity_lost", "detail": "one-by-one quantifier lost"})
                # The post-noun 'một' in 'từng cái một' is distributive, even
                # without punctuation before the next clause. Strip only this
                # grammatical slot, not other cardinals/measurements in the cue.
                numeric_after = re.sub(
                    r"(?<!\w)((?:từng|mỗi)\s+(?:(?!(?:không|một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười)\b)[^\W\d_]+\s+){1,4})một(?!\w)",
                    r"\1", _normalize(numeric_after),
                )
        # C6 compares explicit source classifiers with an indefinite article
        # ('一个博主' -> 'một blogger'). C7 may still drop a non-quantitative
        # article such as 'một câu rất dài'; actual count/measurement contexts
        # remain guarded by the common normalizer.
        if _lexicon_for(source_language) == "vietnamese":
            numeric_before = _vi_numeric_text(_normalize(numeric_before), indefinite=True)
        if _lexicon_for(target_language) == "vietnamese":
            numeric_after = _vi_numeric_text(_normalize(numeric_after), indefinite=True)
    old_neg, new_neg = _negations(negation_before, source_language), _negations(negation_after, target_language)
    if old_neg != new_neg:
        issues.append({"kind": "negation_lost" if old_neg > new_neg else "negation_added",
                       "detail": f"negation count changed: {old_neg} -> {new_neg}"})
    # Counts can match while the polarity moved between clauses: 我不买苹果，我买梨
    # -> Tôi mua táo, tôi không mua lê keeps one negation in both languages and
    # asserts the opposite thing. Comparing per-clause polarity, not just the
    # total, is what closes that false negative.
    if old_neg == new_neg and old_neg:
        old_clauses = _clause_polarity(before, source_language)
        new_clauses = _clause_polarity(after, target_language)
        if (len(old_clauses) > 1 and len(old_clauses) == len(new_clauses)
                and sorted(old_clauses) == sorted(new_clauses)
                and old_clauses != new_clauses):
            issues.append({"kind": "negation_scope_changed", "severity": "hard",
                           "action": "reject",
                           "detail": f"negation moved between clauses: {old_clauses} -> {new_clauses}"})
    ordinal_before, old_ordinals = _ordinal_text(numeric_before, source_language)
    standalone = not translation or _lexicon_for(source_language) != "chinese" or bool(old_ordinals)
    ordinal_after, new_ordinals = _ordinal_text(numeric_after, target_language, standalone=standalone, labelled=bool(old_ordinals))
    if old_ordinals != new_ordinals:
        issues.append({"kind": "ordinal_changed", "detail": "ordinal/sequential slots changed"})
    numbers_before = _numbers(ordinal_before)
    numbers_after = _numbers(ordinal_after)
    if numbers_before != numbers_after:
        # Role-aware retry: a difference that is only about the indefinite
        # article '1' is not a count change. '最正的一個' -> '1 khuôn mặt' is
        # that case, and it is ambiguous rather than wrong, so it is reported
        # as uncertain/review instead of being blocked or silently passed.
        roles_before = _quantity_roles(before, source_language)
        roles_after = _quantity_roles(after, target_language)
        cardinals_before = Counter({k: v for k, v in roles_before.items() if k[1] != ROLE_ARTICLE})
        cardinals_after = Counter({k: v for k, v in roles_after.items() if k[1] != ROLE_ARTICLE})
        if cardinals_before == cardinals_after:
            issues.append({"kind": "article_asymmetry", "severity": "uncertain",
                           "action": "review",
                           "detail": "count differs only in the indefinite article '1'"})
        else:
            issues.append({"kind": "quantity_lost", "detail": "numeric values/counts changed"})
    implied_age = _implied_age(context, before, source_language)
    if implied_age is not None:
        # The translated unit is licensed for exactly this value and only for
        # the age unit; any other unit or value stays a hard finding.
        after_units = _units(ordinal_after)
        if after_units - _units(ordinal_before) == {"age"}:
            measurements = _measurements(ordinal_after)
            if measurements.get((implied_age, "age"), 0):
                ordinal_after = re.sub(
                    r"(?<![\w.])(?:" + "|".join(
                        _phrase_pattern(alias) for alias in sorted(_UNIT_ALIASES["age"], key=len, reverse=True))
                    + r")(?![\w])", " ", ordinal_after)
                numeric_after = re.sub(
                    r"(?<![\w.])(?:" + "|".join(
                        _phrase_pattern(alias) for alias in sorted(_UNIT_ALIASES["age"], key=len, reverse=True))
                    + r")(?![\w])", " ", numeric_after)
                numbers_after = _numbers(ordinal_after)
    if _units(ordinal_before) != _units(ordinal_after) or _measurements(ordinal_before) != _measurements(ordinal_after):
        issues.append({"kind": "unit_lost", "detail": "measurement units or numeric/unit bindings changed"})
    # Punctuation carries the speaker's intent. A question or exclamation must
    # not turn into a flat assertion during translation/compression.
    for old, new, kind in ((old_question, new_question, "question_changed"),
                          ("!" in _normalize(before), "!" in _normalize(after), "exclamation_changed")):
        if old != new:
            issues.append({"kind": kind, "detail": "source speech act changed"})
    return issues


def untranslated_han(text: str, target_language: str | None, *, allowed: Iterable[str] = ()) -> list[str]:
    if _lexicon_for(target_language) != "vietnamese":
        return []
    for term in sorted(allowed, key=len, reverse=True):
        if term:
            text = text.replace(term, "")
    return list(dict.fromkeys(_HAN_RE.findall(text)))


_HARD_KINDS = frozenset({
    "negation_lost", "negation_added", "negation_scope_changed", "quantity_lost",
    "unit_lost", "ordinal_changed", "question_changed", "exclamation_changed",
    "antonym_flip", "term_lost", "unlocalized_text",
    "role_reversal", "role_loss", "relation_loss", "predicate_reversal",
})


def _verdicts(issues: list[dict], *, include_advisories: bool) -> list[dict]:
    """Attach severity/action and apply the opt-in advisory policy.

    Default: only hard findings, so every existing caller keeps rejecting
    exactly what it rejected before. `include_advisories=True` also returns the
    uncertain findings (currently `article_asymmetry`) so a caller can route
    them to review instead of treating them as either pass or reject.
    """
    for issue in issues:
        issue.setdefault("severity", "hard" if issue["kind"] in _HARD_KINDS else "uncertain")
        issue.setdefault("action", "reject" if issue["severity"] == "hard" else "review")
    if include_advisories:
        return issues
    return [issue for issue in issues if issue["severity"] == "hard"]


# ── C7 semantic-role/relation guards ──────────────────────────────────────
# Vietnamese pronouns that can serve as actor or object in a clause.
# This is a closed class, not a whitelist of sample-specific words.
_VI_PRONOUNS = frozenset({
    "tôi", "tao", "mình", "tui",  # 1st person
    "em", "anh", "chị", "bạn", "cậu", "mày",  # 2nd person / familiar
    "nó", "hắn", "nàng", "chàng",  # 3rd person
    "ta", "chúng ta", "chúng tôi", "chúng mình",  # inclusive
    "họ", "chúng nó",  # 3rd person plural
})

# Explicit relation markers: dropping these removes the naming/assertion
# relationship even when the content words survive.
# Only markers containing "tên" are protected because "tên" explicitly
# establishes the naming relation; "gọi là" alone can be dropped when the
# content is preserved (e.g., "Bố cục này gọi là X" → "X" is legitimate).
_VI_RELATION_MARKERS = (
    "tên là", "tên gọi là", "tên của", "là tên", "có tên là", "có tên gọi là",
)


_VI_PARTICIPANT_RE = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(p) for p in sorted(_VI_PRONOUNS, key=len, reverse=True)) + r")(?!\w)"
)
_VI_ROLE_CONNECTIVES = frozenset({"và", "hay", "hoặc", "với", "của", "cũng", "đều", "cả"})


def _extract_pronouns(text: str) -> list[str]:
    """Closed-class participants, including multiword forms and punctuation."""
    return [m.group() for m in _VI_PARTICIPANT_RE.finditer(_normalize(text))]


def _role_frames(text: str) -> list[tuple[str, frozenset[str], str]]:
    """Local participant/predicate/participant evidence, not a semantic parser.

    Never infer an object from a conjunction-subject pair or compare pronoun
    order across independent clauses. Ambiguous ellipsis is advisory.
    """
    frames = []
    for clause in re.split(r"[.,;:!?\n]+", _normalize(text)):
        roles = list(_VI_PARTICIPANT_RE.finditer(clause))
        for left, right in zip(roles, roles[1:]):
            predicate = frozenset(re.findall(r"[^\W_]+", clause[left.end():right.start()]))
            if (left.group() == right.group() or not predicate or predicate <= _VI_ROLE_CONNECTIVES
                    or predicate & {"rằng", "nếu", "khi", "bởi", "vì"}):
                continue
            frames.append((left.group(), predicate, right.group()))
    return frames


def _same_predicate(first: frozenset[str], second: frozenset[str]) -> bool:
    return bool(first & second)


def _extract_relation_markers(text: str) -> list[str]:
    """Extract explicit relation markers from text."""
    text = _normalize(text)
    return [m for m in _VI_RELATION_MARKERS if _contains(text, m)]


def _detect_role_reversal(before: str, after: str) -> bool:
    """Detect actor/object reversal: 'tôi chụp em' -> 'em chụp tôi'.

    A reversal requires:
    - Both before and after have at least 2 pronouns
    - The pronoun sets are the same (same participants)
    - The order is reversed (actor and object swapped)
    """
    old, new = _role_frames(before), _role_frames(after)
    for subject, predicate, obj in old:
        if any(s == subject and o == obj and _same_predicate(predicate, p) for s, p, o in new):
            continue  # The original assertion survived a clause reorder.
        if any(s == obj and o == subject and _same_predicate(predicate, p) for s, p, o in new):
            return True
    return False


def _detect_role_loss(before: str, after: str) -> bool:
    """Detect explicit role loss: 'tôi chụp em' -> 'chụp' (both roles dropped).

    Role loss requires:
    - Before has at least 2 pronouns (actor and object)
    - After has 0 or 1 pronoun (at least one role dropped)
    - The verb/content is preserved (not a complete rewrite)
    """
    remaining = set(_extract_pronouns(after))
    after_words = frozenset(re.findall(r"[^\W_]+", _normalize(after)))
    return any(subject not in remaining and obj not in remaining and _same_predicate(predicate, after_words)
               for subject, predicate, obj in _role_frames(before))


def _detect_relation_loss(before: str, after: str) -> bool:
    """Detect explicit relation loss: 'tên là Vũ' -> 'là Vũ'.

    Relation loss requires:
    - Before has an explicit relation marker
    - After does NOT have that marker
    - The content after the marker is preserved
    """
    before_markers = _extract_relation_markers(before)
    if not before_markers:
        return False

    after_markers = _extract_relation_markers(after)

    # These forms express the same naming relation; spelling is not identity.
    if after_markers or _contains(after, "gọi là") or _contains(after, "mang tên"):
        return False

    # Punctuation cannot hide the name that survived the lost relation.
    before_words = set(re.findall(r"[^\W_]+", _normalize(before)))
    after_words = set(re.findall(r"[^\W_]+", _normalize(after)))
    content_preserved = bool(before_words & after_words)

    return content_preserved


def _semantic_role_issues(before: str, after: str, language: str | None) -> list[dict]:
    """Detect semantic role/relation violations in Vietnamese rewrites."""
    if _lexicon_for(language) != "vietnamese":
        return []

    issues = []

    if _detect_role_reversal(before, after):
        issues.append({
            "kind": "role_reversal",
            "detail": "actor/object reversal detected",
        })

    if _detect_role_loss(before, after):
        issues.append({
            "kind": "role_loss",
            "detail": "explicit actor/object loss detected",
        })

    remaining = set(_extract_pronouns(after))
    after_words = frozenset(re.findall(r"[^\W_]+", _normalize(after)))
    if any((subject in remaining) != (obj in remaining) and _same_predicate(predicate, after_words)
           for subject, predicate, obj in _role_frames(before)):
        issues.append({"kind": "role_ellipsis", "detail": "one explicit participant became implicit",
                       "severity": "uncertain", "action": "review"})

    if _detect_relation_loss(before, after):
        issues.append({
            "kind": "relation_loss",
            "detail": "explicit relation marker loss detected",
        })

    return issues


def _predicate_roles(text: str, language: str | None) -> set[str]:
    """Small explicit predicate inventory, not a general semantic certificate.

    Match compound predicates before their component verbs: 'cho X vay' is
    lending, not borrowing; 'không thấm nước' is not water permeability.
    Multiple opposed predicates are retained, so a sentence discussing buying
    AND selling does not fail merely because their order changes.
    """
    text = _normalize(text)
    language = _lexicon_for(language)
    roles = set()
    if language == "vietnamese":
        if _contains(text, "mua"):
            roles.add("buy")
        if _contains(text, "bán"):
            roles.add("sell")
        if any(_contains(text, phrase) for phrase in ("chống nước", "chống thấm", "không thấm nước")):
            roles.add("waterproof")
        for phrase in ("không thấm nước", "chống thấm nước"):
            text = text.replace(phrase, "")
        if _contains(text, "thấm nước"):
            roles.add("permeable")
        lending = re.compile(r"\bcho\s+(?!(?:phép|biết|thấy|rằng)\b)(?:(?![.!?;,]|\b(?:vay|mượn)\b).){0,60}\b(?:vay|mượn)\b")
        if lending.search(text):
            roles.add("lend")
            text = lending.sub("", text)
        if _contains(text, "vay") or _contains(text, "mượn"):
            roles.add("borrow")
    elif language == "chinese":
        if re.search(r"[买買]", text):
            roles.add("buy")
        if re.search(r"[卖賣]", text):
            roles.add("sell")
        if re.search(r"防水|防渗|防滲|不透水", text):
            roles.add("waterproof")
        text = text.replace("不透水", "")
        if re.search(r"渗水|滲水|透水|漏水", text):
            roles.add("permeable")
        # 借 is ambiguous alone. Only an explicit direction licenses a verdict.
        if re.search(r"借[^。！？；，,;]{0,20}[给給]|借出", text):
            roles.add("lend")
        if re.search(r"[向跟从從][^。！？；，,;]{1,20}借|借入", text):
            roles.add("borrow")
    return roles


def _predicate_reversals(before: str, after: str, source_language, target_language) -> list[dict]:
    old = _predicate_roles(before, source_language)
    new = _predicate_roles(after, target_language)
    issues = []
    for a, b in (("buy", "sell"), ("waterproof", "permeable"), ("lend", "borrow")):
        left, right = old & {a, b}, new & {a, b}
        if len(left) == len(right) == 1 and left != right:
            issues.append({"kind": "predicate_reversal", "detail": f"predicate changed: {sorted(left)} -> {sorted(right)}"})
    return issues


def check_rewrite_meaning(before: str, after: str, target_language: str | None = None,
                          *, protected_terms: Iterable[str] = (),
                          context: GuardContext | None = None,
                          include_advisories: bool = False) -> list[dict]:
    """Reject deterministic invariant violations before accepting any C7 text.

    `context` and `include_advisories` are additive: omitting both reproduces
    the previous behaviour byte for byte.
    """
    if not before.strip() or not after.strip():
        return []
    issues = _invariants(before, after, target_language, target_language,
                         context=context)
    # C7 semantic-role/relation guards
    issues.extend(_semantic_role_issues(before, after, target_language))
    issues.extend(_predicate_reversals(before, after, target_language, target_language))
    for term in protected_terms:
        if _contains(before, term) and not _contains(after, term):
            issues.append({"kind": "term_lost", "token": term, "detail": "explicit protected term lost"})
    for first, second in _ANTONYMS.get(_lexicon_for(target_language), ()):
        old = _polarity_hits(before, first, second)
        new = _polarity_hits(after, first, second)
        if (first in old and second in new and second not in old) or (second in old and first in new and first not in old):
            issues.append({"kind": "antonym_flip", "detail": f"polarity changed: {first} / {second}"})
    return _verdicts(issues, include_advisories=include_advisories)


def check_translation_meaning(before: str, after: str, source_language: str | None,
                              target_language: str | None, *, glossary: dict[str, str],
                              protected_terms: Iterable[str] = (),
                              context: GuardContext | None = None,
                              include_advisories: bool = False) -> list[dict]:
    """C6 cross-language checks; do not compare literal source/target wording.

    `context` supplies the neighbouring cues of the same semantic group so a
    sentence that a cue boundary splits in half can still be judged; it is
    optional and cannot change a fact. `include_advisories` adds the uncertain
    findings, which default to being withheld from existing callers.
    """
    issues = _invariants(before, after, source_language, target_language,
                         translation=True, context=context)
    issues.extend(_predicate_reversals(before, after, source_language, target_language))
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
    return _verdicts(issues, include_advisories=include_advisories)


def check_translation_group_meaning(
        segments: Sequence[GuardSegment | dict], source_language: str | None,
        target_language: str | None, *, glossary: dict[str, str] | None = None,
        protected_terms: Iterable[str] = (), include_advisories: bool = False,
        group_id: str | None = None) -> dict[str, list[dict]]:
    """Judge a semantic validation group; returns issues per segment id.

    A subtitle cue is a display unit, so a speech act can straddle a boundary:
    '看你的鼻翼有沒有超過' + '你的兩個瞳孔的內側' is one question, and either
    half alone is not a speech act. This walks the group once so a
    boundary-split construct is judged as the sentence it belongs to, while
    every per-cue invariant (counts, units, negation, glossary) is still checked
    on the cue that carries it — a count changed inside the group stays blocked,
    and an accepted member is never disturbed by a rejected sibling.

    Raises ValueError on duplicate or empty ids, because a group whose members
    cannot be addressed individually cannot be committed atomically.
    """
    rows = list(segments)
    if not rows:
        return {}

    def field(row, name):
        return row[name] if isinstance(row, dict) else getattr(row, name)

    ids = [field(row, "id") for row in rows]
    if len(set(ids)) != len(ids) or not all(isinstance(i, str) and i.strip() for i in ids):
        raise ValueError("semantic group requires unique non-empty segment ids")
    resolved = group_id or f"group-{ids[0]}-{ids[-1]}"
    # The speech act of the whole group, evaluated once, is what a member is
    # judged against for the group-level construct only.
    source_group = " ".join((field(row, "source_text") or "") for row in rows)
    target_group = " ".join((field(row, "translated_text") or "") for row in rows)
    group_question = (_translation_question(source_group, source_language),
                      _translation_question(target_group, target_language))
    group_exclamation = ("!" in _normalize(source_group), "!" in _normalize(target_group))
    # Boolean group presence is insufficient when each cue is an independent
    # question: the second question cannot pay for the first one's lost act.
    question_members = [sum(_translation_question(field(row, key) or '', language) for row in rows)
        for key, language in [('source_text', source_language), ('translated_text', target_language)]]
    exclamation_members = [sum('!' in _normalize(field(row, key) or '') for row in rows)
        for key in ['source_text', 'translated_text']]

    verdicts: dict[str, list[dict]] = {}
    for index, row in enumerate(rows):
        context = build_guard_context(rows, index, group_id=resolved)
        issues = check_translation_meaning(
            field(row, "source_text"), field(row, "translated_text"),
            source_language, target_language, glossary=glossary or {},
            protected_terms=protected_terms, context=context,
            include_advisories=include_advisories)
        # Suppress a speech-act finding on ONE member only when the group as a
        # whole preserves that speech act. If the group dropped it, the finding
        # stands on every member, so a group cannot hide a real change.
        if group_question[0] == group_question[1] and max(question_members) <= 1:
            issues = [issue for issue in issues if issue["kind"] != "question_changed"]
        if group_exclamation[0] == group_exclamation[1] and max(exclamation_members) <= 1:
            issues = [issue for issue in issues if issue["kind"] != "exclamation_changed"]
        verdicts[ids[index]] = issues
    return verdicts

