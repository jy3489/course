"""The texts of a game: reading its string table and checking how translatable it is.

A game that wants to be translated lists every text it shows in one JSON block inside its own
HTML, in English, with `{placeholders}` for the parts that change:

    <script type="application/json" id="i18n-strings">
      {"your_turn": "Your turn", "wins": "{name} wins!"}
    </script>

The block travels with the game's code (so it is versioned with it, and the AI that writes
the game writes it), and it is the game's whole vocabulary: finite and known, so it can be
translated ahead of need and checked. The game shows texts with `t(key, params)` (see
game_i18n_helper.js), and the platform sends it the translation in its state_changed
message. See docs/game-translation.md.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final

MAX_KEYS: Final = 400
MAX_TEXT_LENGTH: Final = 1000
_KEY: Final = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_BLOCK: Final = re.compile(
    r"""<script\b(?=[^>]*\bid\s*=\s*["']i18n-strings["'])[^>]*>(.*?)</script\s*>""",
    re.S | re.I,
)

# The helper a game includes; also what the game-building agent is told to write.
HELPER_JS: Final = (Path(__file__).with_name("game_i18n_helper.js")).read_text(encoding="utf-8")


# What the game-building agent is told, so that the games it writes can be translated. The
# helper is the real file, so the prompt can never drift from what the platform sends.
PROMPT_RULES: Final = (
    """\
TRANSLATION. Players use many languages, so the client's text must be translatable:
- List EVERY text the client shows in ONE block, in English, and show texts only through \
t(): buttons, labels, titles, hints, errors, results, `aria-label`s, placeholders and text \
drawn on a canvas. Put the block first in the document, then include the helper below \
exactly as it is, in the client's script:

<script type="application/json" id="i18n-strings">
{"your_turn": "Your turn", "wins": "{name} wins!", "moves": "Moves: {count}"}
</script>
<script>
"""
    + HELPER_JS
    + """</script>

- Call applyLocale(message) FIRST in the state_changed handler, before drawing anything.
- One string per sentence, with {placeholders} for the parts that change (names, numbers): \
word order differs between languages, so NEVER build a sentence from pieces \
("name + ' wins'") and never choose a word inside a sentence with a ternary: use two keys \
("your_turn", "their_turn") and pick between whole texts.
- Keys use only letters, digits, _ . - ; texts are plain text, no HTML. Do not make plurals: \
write "Moves: {count}", not "{count} move(s)".
- Player names come from `players` and are not translated. Do not put display text in the \
match state or in `make_move`: store codes ("X", "draw") and turn them into text with t() \
when drawing, because players in one match may use different languages.
- Let the layout survive longer text and right-to-left languages: no fixed widths for \
text, allow wrapping, and use CSS logical properties (margin-inline-start, text-align: \
start, inset-inline-end) with flex or grid instead of left/right.
"""
)


class StringsError(ValueError):
    """The game's string table is there but unusable (the message says why)."""


def extract_strings(code: str) -> dict[str, str] | None:
    """The game's string table, or None if its code has no `i18n-strings` block.
    Raises [StringsError] for a block that is not a JSON object of short texts."""
    match = _BLOCK.search(code)
    if match is None:
        return None
    try:
        table = json.loads(match.group(1))
    except ValueError as error:
        raise StringsError(f"the i18n-strings block is not valid JSON: {error}") from None
    if not isinstance(table, dict):
        raise StringsError("the i18n-strings block must be a JSON object of key: text")
    if len(table) > MAX_KEYS:
        raise StringsError(f"the i18n-strings block has {len(table)} texts; the most is {MAX_KEYS}")
    for key, text in table.items():
        if not _KEY.match(key):
            raise StringsError(f"the key {key[:40]!r} must be letters, digits, _ . - (at most 64)")
        if not isinstance(text, str):
            raise StringsError(f"the text of {key!r} must be a string")
        if len(text) > MAX_TEXT_LENGTH:
            raise StringsError(f"the text of {key!r} is {len(text)} characters; the most is {MAX_TEXT_LENGTH}")
    return table


# What makes a game hard to translate

_USED: Final = re.compile(r"""\bt\(\s*["']([A-Za-z0-9_.\-]+)["']""")
_ASSIGNMENT: Final = re.compile(
    r"(?:\.textContent|\.innerText|\.innerHTML|\.title|\.placeholder|\.alt)\s*\+?=\s*([^;\n]+)"
)
_LITERAL_WITH_WORDS: Final = re.compile(r"""(["'`])((?:\\.|(?!\1).)*?[A-Za-z]{3,}(?:\\.|(?!\1).)*?)\1""")
_PLACEHOLDER: Final = re.compile(r"\{(\w+)\}")


def lint(code: str) -> list[str]:
    """Problems that stop a game from being translated well, in plain words. Empty when the
    game looks fine. The checks are heuristics: they find the common mistakes."""
    try:
        table = extract_strings(code)
    except StringsError as error:
        return [str(error)]
    if table is None:
        return [
            "the game has no i18n-strings block, so only its name and description can be "
            "translated: list every text it shows in "
            '<script type="application/json" id="i18n-strings"> and show them with t()'
        ]
    warnings: list[str] = []
    used = set(_USED.findall(code))
    for key in sorted(used - table.keys()):
        warnings.append(f"the code uses t({key!r}) but the strings block has no {key!r}")
    for key in sorted(table.keys() - used):
        warnings.append(f"the strings block has {key!r}, which the code never shows")
    hard_coded, joined = [], []
    for assignment in _ASSIGNMENT.finditer(_without_block(code)):
        value = assignment.group(1)
        if re.match(r"\s*t\(", value) and not re.search(r"""["'`]\s*\+|\+\s*["'`]""", value):
            continue  # a plain t(...) call
        literals = [m.group(2) for m in _LITERAL_WITH_WORDS.finditer(value)]
        if not literals:
            continue
        if re.search(r"""["'`]\s*\+|\+\s*["'`]""", value):
            joined.append(value.strip()[:70])
        elif not value.lstrip().startswith("t("):
            hard_coded.append(literals[0][:50])
    if hard_coded:
        shown = ", ".join(repr(text) for text in hard_coded[:3])
        more = f" (and {len(hard_coded) - 3} more)" if len(hard_coded) > 3 else ""
        warnings.append(f"text written in the code instead of the strings block: {shown}{more}")
    if joined:
        warnings.append(
            f"{len(joined)} sentence(s) are built by joining pieces (e.g. {joined[0]!r}): "
            "word order differs between languages, so use one string with {placeholders}"
        )
    for key, text in table.items():
        if re.search(r"[<>]|&\w+;", text):
            warnings.append(f"the text of {key!r} contains markup; strings are plain text")
            break
    if re.search(r"fillText|strokeText", code):
        warnings.append("the game draws text on a canvas: make sure it comes from t(), "
                        "and leave room for longer words")
    return warnings


def _without_block(code: str) -> str:
    return _BLOCK.sub("", code)
