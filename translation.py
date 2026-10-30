"""Translating the texts of games (and their names and descriptions) on demand.

A text is translated the first time someone asks for it in a language, by Google Cloud
Translation, and the result is remembered (a [Translation], keyed by language and the hash
of the text), so the same text is never translated, or paid for, twice: not by another
player, another version of the game, or another game that happens to say the same thing.
A person can correct a remembered translation in the backoffice.

Texts are plain text with `{placeholders}` (see game_text.py). Placeholders are protected
from the translator and checked afterwards: a translation that lost or changed one is
refused, and the original is shown instead. Anything that fails (no translator, the
service is down, the daily budget is spent) falls back to the original text, never to an
error. See docs/game-translation.md.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol

from game_platform import i18n, observability
from game_platform.models import Translation
from game_platform.store import Store

logger = logging.getLogger(__name__)

PLACEHOLDER: Final = re.compile(r"\{(\w+)\}")
# One request to Google may carry at most 128 texts; stay well under its size limits too.
_BATCH_TEXTS: Final = 100
_BATCH_CHARACTERS: Final = 20_000


def text_hash(text: str) -> str:
    """The identity of a source text: translations are remembered under this."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class TranslationError(Exception):
    """The translator could not translate (down, refused, or answered nonsense)."""


class Translator(Protocol):
    def translate(self, texts: Sequence[str], target: str) -> list[str]:
        """The translation of every text (in order) into the language [target]."""
        ...


# Protecting placeholders from the translator


def protect(text: str) -> str:
    """[text] as HTML for the translator: markup characters escaped, and each
    `{placeholder}` marked "do not translate"."""
    return PLACEHOLDER.sub(
        lambda match: f'<span translate="no">{match.group(0)}</span>',
        html.escape(text, quote=False),
    )


_UNMARK: Final = re.compile(r"<span\b[^>]*\btranslate=[\"']no[\"'][^>]*>(.*?)</span>", re.S | re.I)


def unprotect(translated: str) -> str:
    """The translator's HTML back to plain text with the placeholders as they were."""
    return html.unescape(_UNMARK.sub(lambda match: match.group(1), translated))


def placeholders_of(text: str) -> list[str]:
    return sorted(PLACEHOLDER.findall(text))


# Google Cloud Translation (the "basic", v2 REST API)

# Google's code for Simplified Chinese is zh-CN; the other languages use ours as they are.
_GOOGLE_CODE: Final = {"zh": "zh-CN"}
_METADATA_TOKEN_URL: Final = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)


class GoogleTranslator:
    """Translates with the Cloud Translation API (v2) over HTTPS, with no extra library.

    Signs in with [api_key] (create one in Google Cloud Console > APIs & Services >
    Credentials, restricted to the Cloud Translation API), or, with no key, with the
    service account of the Google Cloud VM it runs on (the API must be enabled, the account
    needs the "Cloud Translation API User" role, and the VM's access scopes must include
    cloud-platform). [urlopen] and [clock] can be replaced in tests."""

    URL: Final = "https://translation.googleapis.com/language/translate/v2"

    def __init__(
        self,
        api_key: str = "",
        *,
        timeout_seconds: float = 10.0,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._urlopen = urlopen
        self._clock = clock
        self._token = ""
        self._token_expires = 0.0

    def translate(self, texts: Sequence[str], target: str) -> list[str]:
        results: list[str] = []
        batch: list[str] = []
        size = 0
        for text in texts:
            if batch and (len(batch) >= _BATCH_TEXTS or size + len(text) > _BATCH_CHARACTERS):
                results.extend(self._translate_batch(batch, target))
                batch, size = [], 0
            batch.append(text)
            size += len(text)
        if batch:
            results.extend(self._translate_batch(batch, target))
        return results

    def _translate_batch(self, texts: list[str], target: str) -> list[str]:
        body = json.dumps(
            {
                "q": [protect(text) for text in texts],
                "target": _GOOGLE_CODE.get(target, target),
                "format": "html",
            }
        ).encode()
        headers = {"Content-Type": "application/json; charset=utf-8"}
        url = self.URL
        if self._api_key:
            url += "?" + urllib.parse.urlencode({"key": self._api_key})
        else:
            headers["Authorization"] = f"Bearer {self._service_account_token()}"
        answer = self._call(urllib.request.Request(url, data=body, headers=headers, method="POST"))
        try:
            translations = answer["data"]["translations"]
            translated = [unprotect(item["translatedText"]) for item in translations]
        except (KeyError, TypeError) as error:
            raise TranslationError("Google's answer had no translations") from error
        if len(translated) != len(texts):
            raise TranslationError(f"asked for {len(texts)} translations, got {len(translated)}")
        return translated

    def _call(self, request: urllib.request.Request) -> Any:
        # Messages never include the request's URL: it carries the API key.
        try:
            with self._urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = json.loads(error.read().decode("utf-8"))["error"]["message"]
            except (ValueError, KeyError, TypeError, OSError):
                pass
            raise TranslationError(f"Google answered {error.code}: {detail[:300]}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TranslationError(f"could not reach Google: {type(error).__name__}") from None
        except ValueError as error:
            raise TranslationError("Google's answer was not JSON") from error

    def _service_account_token(self) -> str:
        if self._token and self._clock() < self._token_expires:
            return self._token
        request = urllib.request.Request(_METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"})
        try:
            token = self._call(request)
            self._token = token["access_token"]
            self._token_expires = self._clock() + float(token.get("expires_in", 300)) - 60
        except (KeyError, TypeError) as error:
            raise TranslationError("the VM's metadata server gave no access token") from error
        return self._token


class FakeTranslator:
    """A translator that needs no network: "Hello {name}" into es is "[es] Hello {name}".
    For tests and for trying the feature locally. [calls] records what was asked."""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.calls: list[tuple[list[str], str]] = []
        self.fail = fail

    def translate(self, texts: Sequence[str], target: str) -> list[str]:
        self.calls.append((list(texts), target))
        if self.fail is not None:
            raise self.fail
        return [f"[{target}] {text}" for text in texts]


# The service: the memory, the budget, and what to do when the translator fails

Status = Literal["source", "translated", "partial", "unavailable"]


@dataclass(frozen=True)
class Translated:
    """What [TranslationService.translate] found: the translation of each text it could
    (a text missing from [texts] is shown as it was) and how complete that is."""

    texts: dict[str, str]
    # "source": no translation was wanted (English, or an unsupported language);
    # "translated": every text; "partial": some; "unavailable": none.
    status: Status


class TranslationService:
    def __init__(
        self,
        store: Store,
        translator: Translator | None,
        *,
        clock: Callable[[], dt.datetime],
        daily_character_limit: int = 200_000,
        retry_after_failure_seconds: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._translator = translator
        self._clock = clock
        self._daily_character_limit = daily_character_limit
        # One translation call at a time: two players asking for the same new text at once
        # must not pay for it twice.
        self._lock = threading.Lock()
        self._day = ""
        self._characters_today = 0
        # After a failure, the translator is left alone for a while: if Google is down,
        # every move of every match would otherwise wait for it to time out.
        self._retry_after_failure_seconds = retry_after_failure_seconds
        self._monotonic = monotonic
        self._failed_until = 0.0

    @property
    def enabled(self) -> bool:
        return self._translator is not None

    def translate(self, texts: Sequence[str], locale: str) -> Translated:
        wanted = list(dict.fromkeys(text for text in texts if text.strip()))
        code = i18n.normalize_locale(locale)
        if code is None or code == i18n.DEFAULT_LOCALE or not wanted:
            return Translated({}, "source")
        hashes = {text: text_hash(text) for text in wanted}
        found = self._store.get_translations(code, list(hashes.values()))
        missing = [text for text in wanted if hashes[text] not in found]
        if missing and self._translator is not None:
            with self._lock:
                # Another request may have translated them while this one waited.
                found = self._store.get_translations(code, list(hashes.values()))
                missing = [text for text in wanted if hashes[text] not in found]
                if missing:
                    self._translate_missing(missing, code, hashes)
                    found = self._store.get_translations(code, list(hashes.values()))
        result = {text: found[hashes[text]].text for text in wanted if hashes[text] in found}
        if len(result) == len(wanted):
            return Translated(result, "translated")
        return Translated(result, "partial" if result else "unavailable")

    def _translate_missing(self, missing: list[str], code: str, hashes: dict[str, str]) -> None:
        assert self._translator is not None
        if self._monotonic() < self._failed_until:
            return  # it failed a moment ago: the originals are shown meanwhile
        characters = sum(len(text) for text in missing)
        if not self._within_budget(characters):
            return
        started = time.perf_counter()
        try:
            translated = self._translator.translate(missing, code)
            if len(translated) != len(missing):
                raise TranslationError(f"asked for {len(missing)} translations, got {len(translated)}")
        except TranslationError as error:
            self._characters_today -= characters  # nothing was translated: give it back
            self._failed_until = self._monotonic() + self._retry_after_failure_seconds
            self._log_failure(error, code, len(missing), characters)
            return
        good: list[Translation] = []
        for source, text in zip(missing, translated):
            if not text.strip() or placeholders_of(text) != placeholders_of(source):
                observability.log_event(
                    "translation_rejected",
                    f"the {code} translation of a text changed its {{placeholders}}; the original is shown",
                    level=logging.WARNING,
                    log=logger,
                    locale=code,
                    source=source[:300],
                    translated=text[:300],
                )
                continue
            good.append(
                Translation(
                    locale=code,
                    source_hash=hashes[source],
                    source=source,
                    text=text,
                    provider="google",
                    created_at=self._clock(),
                )
            )
        self._store.put_translations(good)
        observability.log_event(
            "translation_done",
            f"translated {len(good)} of {len(missing)} texts into {code}",
            log=logger,
            locale=code,
            texts=len(missing),
            stored=len(good),
            characters=characters,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )

    def _within_budget(self, characters: int) -> bool:
        today = self._clock().date().isoformat()
        if today != self._day:
            self._day, self._characters_today = today, 0
        if self._characters_today + characters > self._daily_character_limit:
            if observability.current().may_alert("translation_budget_exceeded"):
                observability.log_event(
                    "translation_budget_exceeded",
                    f"{self._characters_today} of {self._daily_character_limit} characters "
                    "were translated today; new texts stay in their original language until tomorrow",
                    level=logging.ERROR,
                    alert="minor",
                    log=logger,
                    characters_today=self._characters_today,
                    daily_character_limit=self._daily_character_limit,
                    asked=characters,
                )
            return False
        self._characters_today += characters
        return True

    def _log_failure(self, error: Exception, code: str, texts: int, characters: int) -> None:
        # A down or refusing translator would fail on every request: one alert per cooldown.
        alert = "minor" if observability.current().may_alert("translation_failed") else None
        observability.log_event(
            "translation_failed",
            f"could not translate into {code}: {error}",
            level=logging.WARNING,
            alert=alert,
            log=logger,
            locale=code,
            texts=texts,
            characters=characters,
            error=str(error),
        )
