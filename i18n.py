"""Languages and message catalogs, so every frontend can show the platform in the user's
language without carrying its own translations.

A catalog is a flat JSON file per language in `locales/`: message key -> text, with
`{name}` placeholders. `en.json` is the source of truth, and every other language must
have the same keys and placeholders (the tests check it). Keys are grouped by prefix:

- `error.*`: the platform's error messages. The service raises English messages; a
  message that matches an English template is translated by key, so the service code
  doesn't change when a language is added.
- `status.*`, `end_reason.*`, `seat.*`: labels for the values the API returns.
- `ui.*`: common labels for portals and builders.

Texts have no plural forms: write them so the count stands alone ("Moves: {count}").
"""

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

DEFAULT_LOCALE: Final = "en"
LOCALES_DIR: Final = Path(__file__).parent / "locales"

Direction = Literal["ltr", "rtl"]


@dataclass(frozen=True, kw_only=True)
class Locale:
    code: str
    # The language's own name, for a language picker.
    name: str
    english_name: str
    # The writing direction, so frontends can mirror their layout (<html dir>).
    direction: Direction = "ltr"


LOCALES: Final = (
    Locale(code="en", name="English", english_name="English"),
    Locale(code="es", name="Español", english_name="Spanish"),
    Locale(code="fr", name="Français", english_name="French"),
    Locale(code="de", name="Deutsch", english_name="German"),
    Locale(code="pt", name="Português", english_name="Portuguese"),
    Locale(code="zh", name="中文（简体）", english_name="Chinese (Simplified)"),
    Locale(code="hi", name="हिन्दी", english_name="Hindi"),
    Locale(code="ar", name="العربية", english_name="Arabic", direction="rtl"),
)
LOCALE_CODES: Final = tuple(locale.code for locale in LOCALES)
_LOCALE_BY_CODE: Final = {locale.code: locale for locale in LOCALES}


def get_locale(code: str) -> Locale:
    return _LOCALE_BY_CODE[code]


def normalize_locale(tag: str | None) -> str | None:
    """Maps a language tag such as "es-MX", "pt_BR" or "zh-Hant-TW" to a supported
    language code ("es", "pt", "zh"), or None if the language isn't supported. Only the
    language part counts, so every Chinese variant gets Simplified Chinese."""
    if not tag:
        return None
    primary = tag.strip().lower().replace("_", "-").split("-")[0]
    return primary if primary in _LOCALE_BY_CODE else None


def parse_accept_language(header: str | None) -> list[str]:
    """The language tags of an Accept-Language header, best first. Tags with q=0 and
    malformed parts are dropped."""
    if not header:
        return []
    ranked: list[tuple[float, int, str]] = []
    for position, part in enumerate(header.split(",")):
        tag, *params = (piece.strip() for piece in part.split(";"))
        if not tag or tag == "*":
            continue
        quality = 1.0
        for param in params:
            if param.startswith("q="):
                try:
                    quality = float(param[2:])
                except ValueError:
                    quality = 0.0
        if quality > 0:
            ranked.append((-quality, position, tag))
    return [tag for _, _, tag in sorted(ranked)]


def negotiate(
    *,
    explicit: str | None = None,
    saved: str | None = None,
    accept_language: str | None = None,
) -> str:
    """The language to answer a request in. In order: the caller's explicit choice
    (the `lang` query parameter), the language saved on the caller's account, the first
    supported language of Accept-Language, and English."""
    for tag in (explicit, saved):
        code = normalize_locale(tag)
        if code:
            return code
    for tag in parse_accept_language(accept_language):
        code = normalize_locale(tag)
        if code:
            return code
    return DEFAULT_LOCALE


@lru_cache(maxsize=None)
def _load(code: str) -> dict[str, str]:
    path = LOCALES_DIR / f"{code}.json"
    messages = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(messages, dict), path
    return messages


def messages(code: str, *, fallback: bool = True, prefix: str = "") -> dict[str, str]:
    """The catalog of [code]. With [fallback], keys it lacks are filled in from English,
    so a partly translated language is still complete. [prefix] keeps only matching keys,
    e.g. "ui." for the labels a portal needs."""
    result = {**_load(DEFAULT_LOCALE), **_load(code)} if fallback else dict(_load(code))
    return {key: text for key, text in result.items() if key.startswith(prefix)}


def catalog_version(code: str, *, fallback: bool = True, prefix: str = "") -> str:
    """A short hash of what [messages] returns, for caching: it changes exactly when a
    text does."""
    body = json.dumps(
        messages(code, fallback=fallback, prefix=prefix), sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(body.encode()).hexdigest()[:12]


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def translate(key: str, locale: str = DEFAULT_LOCALE, /, **params: object) -> str:
    """The text of [key] in [locale] (English if it lacks the key, the key itself if
    English lacks it too), with `{name}` placeholders filled from [params]. [key] and
    [locale] are positional-only so that a placeholder may be called "locale" or "key"."""
    template = _load(locale).get(key) or _load(DEFAULT_LOCALE).get(key) or key
    return _PLACEHOLDER.sub(
        lambda match: str(params.get(match.group(1), match.group(0))), template
    )


@lru_cache(maxsize=None)
def _error_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """One pattern per English `error.*` text, with each placeholder matching anything."""
    patterns = []
    for key, template in _load(DEFAULT_LOCALE).items():
        if not key.startswith("error."):
            continue
        pieces = _PLACEHOLDER.split(template)
        # split() alternates literal text and placeholder names.
        regex = "".join(
            f"(?P<{piece}>.+?)" if index % 2 else re.escape(piece)
            for index, piece in enumerate(pieces)
        )
        patterns.append((key, re.compile(regex, re.DOTALL)))
    return tuple(patterns)


UNKNOWN_ERROR_CODE: Final = "unknown"


def localize_error(message: str, locale: str) -> tuple[str, str, dict[str, str]]:
    """Translates an English error message: returns (text in [locale], error code,
    parameters). The code is the message's key without "error." and is the same in every
    language, so clients can branch on it. A message with no catalog entry stays as it is
    with the code "unknown". English messages are never rewritten."""
    for key, pattern in _error_patterns():
        match = pattern.fullmatch(message)
        if match:
            params = match.groupdict()
            text = message if locale == DEFAULT_LOCALE else translate(key, locale, **params)
            return text, key.removeprefix("error."), params
    return message, UNKNOWN_ERROR_CODE, {}
