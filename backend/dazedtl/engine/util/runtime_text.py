"""Pure runtime-code protection and validation, shared by translation and review."""

import re
import unicodedata
from collections import Counter

# ===== Placeholder Protection System =====
# Patterns to protect from translation (sound effects, control codes, etc.)
# A control code after its backslash run: a word with an optional [parameter],
# such as \C[2], \I[5] or \G, or one of the symbol codes, such as \{ or \!.
CONTROL_CODE = (
    r'(?:[A-Za-z_][A-Za-z0-9_]*(?:\[(?:[^\[\]]|\[[^\]]*\])*\])?'
    r'|[{}.!|^><#$@,\-])'
)
_GENERAL_CONTROL_PATTERN = r'[\\]+' + CONTROL_CODE
_ORPHAN_BACKSLASH_PATTERN = r'[\\]+(?=[^\W\d_])'

# Codes that name a game asset, such as a sound file, which the game finds
# by the name as the source spells it, whatever language it is in.
ASSET_PATTERNS = [
    r'\\SE\[[^\]]+\]',      # \SE[sound_effect_name]
    r'\\ME\[[^\]]+\]',      # \ME[music_effect_name]
    r'\\BGM\[[^\]]+\]',     # \BGM[background_music_name]
    r'\\BGS\[[^\]]+\]',     # \BGS[background_sound_name]
    r'_pum\[[^\]]+\]',      # _pum[name]
    r'\\VS\[[^\]]+\]',      # \VS[name]
]

PROTECTED_PATTERNS = [
    *ASSET_PATTERNS,
    # General RPG Maker/plugin controls. Preserve the full spelling, parameter,
    # slash count, and order rather than asking the model to reproduce them.
    _GENERAL_CONTROL_PATTERN,
    # A backslash before a non-ASCII word is not recognized as a control code,
    # but translating that word can turn it into one (``\ヘレン`` ->
    # ``\Helen``). Protect the slash run separately; target restoration escapes
    # odd runs so they remain literal instead of becoming runtime commands.
    _ORPHAN_BACKSLASH_PATTERN,
]

_CONTROL_CODE_RE = re.compile(_GENERAL_CONTROL_PATTERN)
_ORPHAN_BACKSLASH_RE = re.compile(r'^[\\]+$')

# A code whose argument is text the player reads, drawn with a mark:
# CombineCharacterEC's emphasis dots \EC[text], handakuten \PS[text],
# strikethroughs \AT[text] and \WAT[text] and underline \UL[text]. Its
# shell (\EC[ and ]) is a code, its argument is translated. The plugin reads
# the argument as /^\[(.+?)\]/, so the argument is never empty and never
# holds a ] or a row break; RPG Maker reads the code's name in any case.
# (\VS[text] shares its name with a voice code, an asset, so it stays one.)
TEXT_CODE = re.compile(r"([\\]+(?:EC|PS|AT|WAT|UL)\[)([^\]\r\n]*)(\])", re.IGNORECASE)
# Two spans of one text code that a single space separates, which a row
# draws as one span.
_TEXT_CODE_PAIR = re.compile(
    r"(?P<open>[\\]+(?:EC|PS|AT|WAT|UL)\[)(?P<text>[^\]\r\n]*)\] (?P=open)",
    re.IGNORECASE,
)


def code_text(text):
    """*text* with each text code (TEXT_CODE) replaced by its argument, the
    text the player reads."""
    text = str(text)
    return TEXT_CODE.sub(r"\2", text) if "\\" in text else text


def text_code_shells(text):
    """*text* with each text code (TEXT_CODE) written as its shell, \\EC[],
    followed by its argument, so code checks compare the shells while the
    argument is translated like the text around it. Spans of one code that
    only spaces or row breaks separate count as one, as they draw."""
    text = str(text)
    if "\\" not in text:
        return text
    parts, end, shell = [], 0, None
    for match in TEXT_CODE.finditer(text):
        between = text[end:match.start()]
        same = shell == match.group(1) and not between.strip()
        parts += [between, "" if same else match.group(1) + match.group(3), f" {match.group(2)} "]
        end, shell = match.end(), match.group(1)
    return "".join(parts) + text[end:]


def joined_text_codes(text):
    """*text* with spans of one text code that a single space separates
    written as one span, as a wrapped line writes the words of a span that
    share a row."""
    text = str(text)
    joined = None
    while "\\" in text and joined != text:
        joined = text
        text = _TEXT_CODE_PAIR.sub(r"\g<open>\g<text> ", text)
    return text


def split_text_codes(text):
    """*text* with each text code holding several words written as one span
    per word, so text that breaks at its spaces can break between them;
    joined_text_codes writes the ones a row keeps together as one again."""
    if "\\" not in str(text):
        return str(text)

    def per_word(match):
        words = match.group(2).split()
        if len(words) < 2:
            return match.group(0)
        return " ".join(match.group(1) + word + match.group(3) for word in words)

    return TEXT_CODE.sub(per_word, str(text))


def empty_text_codes(text):
    """The text codes in *text* with no argument to draw, which the plugin
    cannot read: the game stops on them."""
    return [match.group(0) for match in TEXT_CODE.finditer(str(text)) if not match.group(2).strip()]

# The placeholder protect_script_codes puts in a protected code's place.
_PLACEHOLDER = "__PROTECTED_{}__"
_PROTECTED_RE = re.compile('|'.join(f'({pattern})' for pattern in PROTECTED_PATTERNS))
# A protected code or the placeholder that holds its place in a request.
_PROTECTED_OR_PLACEHOLDER_RE = re.compile(
    _PLACEHOLDER.format(r"\d+") + "|" + _PROTECTED_RE.pattern
)

# Half-width kana letters, the half-width part of util/source_text LETTERS.
HALF_WIDTH_LETTERS = "\uff66-\uff9d"
# Half-width text the model reads better full-width: a word of two or more
# half-width kana letters with their voicing marks, and the sentence
# punctuation ｡｢｣､, which the quote conversion reads full-width. A lone
# half-width kana or mark and the middle dot ･ stay as the game wrote them:
# they are more often part of a kaomoji, such as the ﾉ, ﾟ and ･ of
# (ﾉﾟ∀ﾟ)ﾉ or (･ω･), and NFKC turns a lone ﾟ into a combining mark, which a
# model gives back and the game then shows mangled.
_HALF_WIDTH_TEXT_RE = re.compile(
    rf"[\uff61-\uff64]+|(?:[{HALF_WIDTH_LETTERS}][\uff9e\uff9f]*){{2,}}"
)


def _escaped_orphan_backslash(code):
    """Return an even slash run so RPG Maker treats it as literal text."""
    value = str(code)
    if _ORPHAN_BACKSLASH_RE.fullmatch(value) and len(value) % 2:
        return value + "\\"
    return value


def extract_control_codes(text):
    """Return runtime control tokens in source order."""
    if not isinstance(text, str) or not text:
        return []
    return _CONTROL_CODE_RE.findall(text)


def without_protected_codes(text):
    """Return text without the codes protect_script_codes protects, or the
    placeholders a protected request holds in their place."""
    return _PROTECTED_OR_PLACEHOLDER_RE.sub("", code_text(text))


def split_protected_codes(text):
    """Return text split around the codes protect_script_codes protects: the
    text between them at even indexes, each code at the odd index after it."""
    text = str(text)
    parts, start = [], 0
    for match in _PROTECTED_RE.finditer(text):
        parts += [text[start:match.start()], match.group()]
        start = match.end()
    return parts + [text[start:]]


def without_control_codes(text):
    """Return text without its runtime control tokens and their arguments,
    such as a font name in ``\\fn[和風]``, which translation leaves alone."""
    return _CONTROL_CODE_RE.sub("", str(text))


# The codes validate_control_codes keeps: every control code and the asset
# codes without a backslash, such as ``_pum[...]``.
_CHECKED_CODE_RE = re.compile('|'.join([_GENERAL_CONTROL_PATTERN, *ASSET_PATTERNS]))


def _mask_mapped_control_codes(text, replacements):
    """Mask restored placeholder codes regardless of their translated order."""
    masked = str(text)
    missing = []

    # Match exact restored codes before using the generic parser. This matters
    # for unparameterized codes such as ``\vc``: once restored before English
    # text, ``\vcThat's`` would otherwise be greedily parsed as ``\vcThat``.
    # Orphan slash placeholders are also masked. Cached/final translations may
    # contain their even, literal-safe form, so prefer that form when present.
    # Search from the beginning for each occurrence so normal translation word
    # order may move standalone values/icons without making them look missing.
    for original in replacements.values():
        code = str(original)
        is_orphan_backslash = _ORPHAN_BACKSLASH_RE.fullmatch(code) is not None
        if _CHECKED_CODE_RE.fullmatch(code) is None and not is_orphan_backslash:
            continue
        matched_code = (
            _escaped_orphan_backslash(code) if is_orphan_backslash else code
        )
        index = masked.find(matched_code)
        if index < 0 and matched_code != code:
            matched_code = code
            index = masked.find(matched_code)
        if index < 0:
            missing.append(code)
            continue
        end = index + len(matched_code)
        masked = masked[:index] + (" " * len(matched_code)) + masked[end:]

    return masked, missing


_FORMAT_SCOPE_RE = re.compile(r"\\(?:[cC]\[[^\]]+\]|[{}><])")


def _format_scope_signature(text):
    """Return structural open/close order for known stateful formatting codes."""
    signatures = {"color": [], "font": [], "speed": []}
    for code in _FORMAT_SCOPE_RE.findall(str(text)):
        lowered = code.lower()
        if lowered.startswith(r"\c["):
            parameter = code[3:-1].strip()
            signatures["color"].append("close" if parameter == "0" else "open")
        elif code == r"\{":
            signatures["font"].append("open")
        elif code == r"\}":
            signatures["font"].append("close")
        elif code == r"\>":
            signatures["speed"].append("open")
        elif code == r"\<":
            signatures["speed"].append("close")
    return signatures


def validate_control_codes(original_items, translated_items, replacements_by_line=None):
    """Require exact control tokens while allowing translation-driven movement.

    ``replacements_by_line`` is the per-line placeholder mapping produced by
    :func:`protect_script_codes`. Known restored codes are matched exactly and
    masked before the generic control-code regex runs. This avoids treating
    adjacent English letters as part of an unparameterized code name. Token
    spelling, parameters, and counts remain strict, while standalone tokens may
    move with translated grammar. Known formatting scopes must retain their
    open/close structure.
    """
    originals = original_items if isinstance(original_items, list) else [original_items]
    translations = translated_items if isinstance(translated_items, list) else [translated_items]
    if len(originals) != len(translations):
        return False, [f"line count differs ({len(originals)} source, {len(translations)} translated)"]

    errors = []
    for index, (original, translated) in enumerate(zip(originals, translations), start=1):
        replacements = (
            replacements_by_line.get(index - 1, {})
            if replacements_by_line
            else {}
        )
        emptied = Counter(empty_text_codes(translated)) - Counter(empty_text_codes(original))
        if emptied:
            errors.append(f"Line{index}: text code with nothing to draw {list(emptied.elements())}")
            continue
        # A text code's argument is translated: its shell is the code.
        original, translated = text_code_shells(original), text_code_shells(translated)
        source_text, source_mapping_missing = _mask_mapped_control_codes(
            original, replacements
        )
        target_text, target_mapping_missing = _mask_mapped_control_codes(
            translated, replacements
        )
        source_sequence = _CHECKED_CODE_RE.findall(source_text)
        target_sequence = _CHECKED_CODE_RE.findall(target_text)

        if source_mapping_missing:
            errors.append(
                f"Line{index}: protected-code mapping absent from source "
                f"{source_mapping_missing}"
            )
            continue
        if target_mapping_missing:
            errors.append(
                f"Line{index}: missing protected codes {target_mapping_missing}"
            )
            continue

        source_codes = Counter(source_sequence)
        target_codes = Counter(target_sequence)
        if source_codes != target_codes:
            missing = list((source_codes - target_codes).elements())
            extra = list((target_codes - source_codes).elements())
            details = []
            if missing:
                details.append(f"missing {missing}")
            if extra:
                details.append(f"extra/altered {extra}")
            errors.append(f"Line{index}: " + "; ".join(details))
            continue

        source_scopes = _format_scope_signature(original)
        target_scopes = _format_scope_signature(translated)
        changed_scopes = [
            name for name in source_scopes
            if source_scopes[name] != target_scopes[name]
        ]
        if changed_scopes:
            errors.append(
                f"Line{index}: formatting scope order changed for "
                + ", ".join(changed_scopes)
            )
    return not errors, errors


def validate_unmasked_control_codes(source, translated):
    """validate_control_codes for one line its request sent with its codes
    as the source holds them, unmasked, as Assistant-led sends them.

    Every code comes back as the source spells it, in count and argument,
    as Guided restores each from its placeholder: a font, face or picture
    name such as ``\\fn[和風]`` and an asset code such as ``\\SE[タイプライター]``
    or ``_pum[名前]`` alike. Only a ruby (util/source_text RUBY), which Guided
    sends as its base, may be translated or left out.
    """
    from util.source_text import RUBY

    source, translated = str(source), str(translated)
    rubies = len(RUBY.findall(source))
    if rubies:
        # A space, so the codes on either side stay apart; a ruby the
        # translation adds is an extra code.
        source = RUBY.sub(" ", source)
        translated = RUBY.sub(" ", translated, count=rubies)
    codes = split_protected_codes(text_code_shells(source))[1::2]
    return validate_control_codes(
        source,
        translated,
        {0: {_PLACEHOLDER.format(index): code for index, code in enumerate(codes)}},
    )


def protect_script_codes(text):
    """
    Replace script codes (like \\SE[タイプライター]) with unique placeholders before translation.
    Returns: (protected_text, replacements_dict)
    """
    if not text or not isinstance(text, str):
        return text, {}

    # Normalize curly/smart quotes to ASCII equivalents BEFORE building the JSON
    # payload.  When these characters appear inside a JSON string value the AI
    # tends to treat them as regular ASCII double-quotes, which makes the value
    # appear empty (e.g. `"スキルを"リセットする` → AI sees empty + stray text).
    # This mirrors the identical normalization already applied to the AI's OUTPUT
    # inside extractTranslation's translation_table.
    quote_norm_table = str.maketrans({
        '\u201C': "'",  # " left double quotation mark
        '\u201D': "'",  # " right double quotation mark
        '\uFF02': "'",  # ＂ fullwidth quotation mark
        '\u2018': "'",  # ' left single quotation mark
        '\u2019': "'",  # ' right single quotation mark
        '\u201B': "'",  # ‛ single high-reversed-9 quotation mark
        '\u02BC': "'",  # ʼ modifier letter apostrophe
        '\uFF07': "'",  # ＇ fullwidth apostrophe
    })
    text = text.translate(quote_norm_table)

    # Convert half-width katakana words to full-width katakana so the AI
    # recognises them as Japanese text and translates them correctly.
    # NFKC is applied only to matched half-width spans (_HALF_WIDTH_TEXT_RE)
    # to avoid altering intentional fullwidth Latin/digit characters or a
    # kaomoji elsewhere in the string.
    text = _HALF_WIDTH_TEXT_RE.sub(lambda m: unicodedata.normalize('NFKC', m.group(0)), text)

    replacements = {}
    protected_text = text
    counter = 0

    def placeholder_for(original):
        nonlocal counter
        placeholder = _PLACEHOLDER.format(counter)
        replacements[placeholder] = original
        counter += 1
        return placeholder

    def replace_match(match):
        # A text code's argument is translated between placeholders for its
        # shell.
        text_code = TEXT_CODE.fullmatch(match.group(0))
        if text_code:
            return (
                placeholder_for(text_code.group(1))
                + text_code.group(2)
                + placeholder_for(text_code.group(3))
            )
        return placeholder_for(match.group(0))

    protected_text = _PROTECTED_RE.sub(replace_match, protected_text)

    return protected_text, replacements


def restore_script_codes(text, replacements, escape_orphan_backslashes=False):
    """
    Restore protected script codes from placeholders after translation.

    When ``escape_orphan_backslashes`` is true, odd slash-only replacements are
    made even. This preserves a literal backslash without allowing translated
    ASCII text immediately after it to become a new RPG Maker control code.
    """
    if not text or not replacements:
        return text

    if isinstance(text, str):
        result = text
        for placeholder, original in replacements.items():
            restored = (
                _escaped_orphan_backslash(original)
                if escape_orphan_backslashes
                else original
            )
            result = result.replace(placeholder, restored)
        return result
    elif isinstance(text, list):
        return [
            restore_script_codes(
                item,
                replacements,
                escape_orphan_backslashes=escape_orphan_backslashes,
            )
            for item in text
        ]
    else:
        return text


# A slash run the translation of an orphan slash's word starts with: before
# a word, as a code without an argument such as ``\\Helen``, or before text
# still untranslated, alone.
_ORPHAN_WORD_RE = re.compile(r"([\\]+)(?:[A-Za-z_][A-Za-z0-9_]*)?")


def escaped_orphan_backslashes(source, translated):
    """*translated*, a line Assistant-led sent with its codes unmasked, with
    each odd slash run its *source* holds before text no code names, such as
    the slash of ``\\ヘレン``, made even as Guided restores it
    (_escaped_orphan_backslash), so ``\\Helen`` is written ``\\\\Helen`` and the
    game shows the name instead of reading an unknown code. A code the
    source holds, spelled as it spells it, and any code with an argument,
    such as a translated ruby, are left alone."""
    codes = split_protected_codes(source)[1::2]
    if not any(_ORPHAN_BACKSLASH_RE.fullmatch(code) for code in codes):
        return translated
    kept = Counter(code for code in codes if not _ORPHAN_BACKSLASH_RE.fullmatch(code))
    parts = split_protected_codes(translated)
    for index in range(1, len(parts), 2):
        code = parts[index]
        held = next((held for held in kept if kept[held] and code.startswith(held)), None)
        if held is not None:
            kept[held] -= 1
            continue
        word = _ORPHAN_WORD_RE.fullmatch(code)
        if word:
            parts[index] = _escaped_orphan_backslash(word.group(1)) + code[word.end(1):]
    return "".join(parts)


def _reprotect_cached_codes(text, replacements):
    """Reapply known placeholders before validating a restored cache value."""
    protected = str(text)
    for placeholder, original in replacements.items():
        code = str(original)
        cached_code = _escaped_orphan_backslash(code)
        index = protected.find(cached_code)
        if index < 0 and cached_code != code:
            cached_code = code
            index = protected.find(cached_code)
        if index < 0:
            continue
        end = index + len(cached_code)
        protected = protected[:index] + placeholder + protected[end:]
    return protected
