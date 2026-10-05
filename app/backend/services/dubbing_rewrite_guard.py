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
from fractions import Fraction
from typing import Iterable

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
                # C7; only 'ba câu' and above are counted objects.
                return match.group()
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
    # A bare classifier with no numeral ('a girl', 'a root of finger') is an
    # indefinite article, which Vietnamese renders as 'một'. Count it so both
    # sides agree instead of reporting the target's article as a lost number.
    # 个 that already belongs to a duration alias (個月/個星期) is a unit, not
    # an article, so it must not be turned into a count.
    text = re.sub(r"(?<![\d零〇一二三四五六七八九两兩十百千万萬])(?<![各每這这那哪兒那個这个這個幾几數数好])(个|個)(?![月周週天年秒分刻小時时])", r"1\1", text)
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


def _ordinal_text(text: str, language: str | None, *, standalone: bool = True) -> tuple[str, list[str]]:
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
        pattern = (r"第\s*(" + _ZH_NUMBER + r"|\d+)(?=\s*" + _ZH_CLASSIFIER + r")"
                   r"|下\s*(?:一\s*(?=" + _ZH_CLASSIFIER + r")|(?=[个個次期章节節集步页頁轮輪年月天周週]))|接下[来來]")
    elif _lexicon_for(language) == "vietnamese":
        number = r"(?:nhất|\d+|(?:" + _VI_NUMBER_WORDS + r")(?:\s+(?:" + _VI_NUMBER_WORDS + r"))*)"
        # A standalone 'thứ hai' can be Monday. Require rank context unless
        # the source already has an ordinal slot; never guess a calendar rank.
        context = "" if standalone else r"(?:video|lần|kỳ|chương|tập|bước|trang|vòng|mục|phần|đoạn|bài|người|chiếc|cái)\s+"
        pattern = (r"(?<!\w)" + context + r"(?:thứ\s+(" + number + r")|đầu tiên)(?!\w)"
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
                    if alias == "月" and re.match(r"亮", text[match.end():]):
                        # 月亮 is the moon, not a month count.
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
        return "?" in text or interrogative
    return "?" in text or (_lexicon_for(language) == "chinese" and bool(
       re.search(r"[吗嗎](?=\s*(?:[。，,;；。.!?！]|[嗯啊呃]|$))", text)
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
        particles += 1
        return prefix + " "

    pattern = (r"(?P<prefix>[^?.!,;]+?)\s+(?:không|chưa|chứ|hả|hở|nhỉ|ư|à)"
               r"(?:\s+(?:ạ|nhỉ|vậy))?(?=\s*(?:[?.,!;]|$))")
    text = re.sub(pattern, strip_particle, _normalize(text))
    return text, bool(particles)


def _invariants(before: str, after: str, source_language: str | None,
                target_language: str | None, *, translation: bool = False) -> list[dict]:
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
    ordinal_before, old_ordinals = _ordinal_text(numeric_before, source_language)
    standalone = not translation or _lexicon_for(source_language) != "chinese" or bool(old_ordinals)
    ordinal_after, new_ordinals = _ordinal_text(numeric_after, target_language, standalone=standalone)
    if old_ordinals != new_ordinals:
        issues.append({"kind": "ordinal_changed", "detail": "ordinal/sequential slots changed"})
    if _numbers(ordinal_before) != _numbers(ordinal_after):
        issues.append({"kind": "quantity_lost", "detail": "numeric values/counts changed"})
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
    issues = _invariants(before, after, source_language, target_language, translation=True)
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
