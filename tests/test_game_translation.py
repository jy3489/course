"""Translating games' texts: the string table, Google Translate, the memory, the endpoints."""

import datetime as dt
import io
import json
import logging
import threading
import time
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from game_platform import agent, game_text, translation
from game_platform.json_file_store import JsonFileStore
from game_platform.models import Translation
from game_platform.store import InMemoryStore
from game_platform.tests.helpers import Api, FakeClock
from game_platform.translation import (
    FakeTranslator,
    GoogleTranslator,
    TranslationError,
    TranslationService,
    protect,
    text_hash,
    unprotect,
)

GAME = """<!doctype html><html><body><div id="status"></div>
<script type="application/json" id="i18n-strings">
{"your_turn": "Your turn", "wins": "{name} wins!", "moves": "Moves: {count}"}
</script>
<script>
const SOURCE_STRINGS = JSON.parse(document.getElementById("i18n-strings").textContent);
function t(key, params) { return SOURCE_STRINGS[key]; }
function render(m) { document.getElementById("status").textContent = t("your_turn"); t("wins", {name: "A"}); t("moves", {count: 1}); }
</script></body></html>"""


def events(caplog: pytest.LogCaptureFixture, name: str | None = None) -> list[dict[str, Any]]:
    found = []
    for record in caplog.records:
        event = getattr(record, "_event", None)
        if event and (name is None or event == name):
            found.append({"event": event, "alert": getattr(record, "_alert", None),
                          "level": record.levelname, **(getattr(record, "_fields", None) or {})})
    return found


@pytest.fixture(autouse=True)
def _log_everything(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


# Protecting {placeholders} from the translator


def test_placeholders_are_marked_do_not_translate_and_come_back_unchanged() -> None:
    marked = protect("{name} wins in {count} moves & <b>fun</b>")
    assert marked == (
        '<span translate="no">{name}</span> wins in <span translate="no">{count}</span> '
        "moves &amp; &lt;b&gt;fun&lt;/b&gt;"
    )
    assert unprotect(marked) == "{name} wins in {count} moves & <b>fun</b>"
    # what a translator might send back
    assert unprotect('¡<span translate="no">{name}</span> gana! &quot;ok&quot; &#39;') == "¡{name} gana! \"ok\" '"


def test_a_texts_identity_is_its_hash() -> None:
    assert text_hash("Your turn") == text_hash("Your turn") != text_hash("your turn")
    assert len(text_hash("x")) == 32


# Google Cloud Translation


class FakeHttp:
    """Stands in for urllib's urlopen: records requests, answers from [answer]."""

    def __init__(self, answer: Any = None, error: Exception | None = None) -> None:
        self.requests: list[Any] = []
        self.answer, self.error = answer, error

    def __call__(self, request: Any, timeout: float = 0) -> Any:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        answer = self.answer(request) if callable(self.answer) else self.answer
        body = io.BytesIO(json.dumps(answer).encode())
        body.__enter__ = lambda: body  # type: ignore[attr-defined]
        body.__exit__ = lambda *args: None  # type: ignore[attr-defined]
        return body


def echo_translations(request: Any) -> dict[str, Any]:
    """Google's answer: every q text, "translated" by wrapping it, HTML-escaped as Google does."""
    if "metadata" in request.full_url:
        return {"access_token": "tok-1", "expires_in": 3600}
    queries = json.loads(request.data)["q"]
    return {"data": {"translations": [{"translatedText": f"¡{q}!"} for q in queries]}}


def test_google_translator_sends_protected_html_with_the_key_in_the_url() -> None:
    http = FakeHttp(echo_translations)
    result = GoogleTranslator("KEY-1", urlopen=http).translate(["{name} wins!", "Hi & bye"], "zh")
    assert result == ["¡{name} wins!!", "¡Hi & bye!"]
    (request,) = http.requests
    assert request.full_url == GoogleTranslator.URL + "?key=KEY-1"
    body = json.loads(request.data)
    assert body["target"] == "zh-CN" and body["format"] == "html"
    assert body["q"] == ['<span translate="no">{name}</span> wins!', "Hi &amp; bye"]
    assert "Authorization" not in dict(request.header_items())


def test_a_long_list_is_sent_in_batches() -> None:
    http = FakeHttp(echo_translations)
    result = GoogleTranslator("k", urlopen=http).translate([f"text {i}" for i in range(230)], "es")
    assert len(result) == 230 and result[229] == "¡text 229!"
    assert [len(json.loads(r.data)["q"]) for r in http.requests] == [100, 100, 30]


def test_without_a_key_the_vms_service_account_signs_in_and_the_token_is_reused() -> None:
    http = FakeHttp(echo_translations)
    translator = GoogleTranslator("", urlopen=http, clock=lambda: 100.0)
    translator.translate(["a"], "es")
    translator.translate(["b"], "es")
    urls = [r.full_url for r in http.requests]
    assert sum("metadata.google.internal" in url for url in urls) == 1  # one token for both calls
    api_calls = [r for r in http.requests if "metadata" not in r.full_url]
    assert [dict(r.header_items())["Authorization"] for r in api_calls] == ["Bearer tok-1"] * 2
    assert all("key=" not in r.full_url for r in api_calls)


def test_google_errors_become_translation_errors_that_never_contain_the_key() -> None:
    forbidden = urllib.error.HTTPError(
        "https://translation.googleapis.com/x?key=SECRET-KEY", 403, "Forbidden", {},  # type: ignore[arg-type]
        io.BytesIO(json.dumps({"error": {"message": "API key not valid"}}).encode()),
    )
    for error, expected in [
        (forbidden, "Google answered 403: API key not valid"),
        (urllib.error.URLError("no route to host"), "could not reach Google"),
        (TimeoutError(), "could not reach Google"),
    ]:
        with pytest.raises(TranslationError, match=expected) as raised:
            GoogleTranslator("SECRET-KEY", urlopen=FakeHttp(error=error)).translate(["a"], "es")
        assert "SECRET-KEY" not in str(raised.value)
    with pytest.raises(TranslationError, match="no translations"):
        GoogleTranslator("k", urlopen=FakeHttp({"nope": 1})).translate(["a"], "es")
    with pytest.raises(TranslationError, match="got 1"):
        GoogleTranslator("k", urlopen=FakeHttp({"data": {"translations": [{"translatedText": "x"}]}})).translate(["a", "b"], "es")


# The memory


def make_service(translator: Any, *, limit: int = 200_000, clock: Any = None) -> tuple[TranslationService, InMemoryStore]:
    store = InMemoryStore()
    return TranslationService(store, translator, clock=clock or FakeClock(), daily_character_limit=limit), store


def test_english_and_unknown_languages_need_no_translation() -> None:
    service, _ = make_service(FakeTranslator())
    for locale in ("en", "en-US", "xx", ""):
        assert service.translate(["Your turn"], locale).status == "source"
    assert service.translate([], "es").status == "source"
    assert service.translate(["  "], "es").status == "source"


def test_a_text_is_translated_the_first_time_and_remembered_after() -> None:
    fake = FakeTranslator()
    service, store = make_service(fake)
    first = service.translate(["Your turn", "{name} wins!"], "es")
    assert first.status == "translated"
    assert first.texts == {"Your turn": "[es] Your turn", "{name} wins!": "[es] {name} wins!"}
    again = service.translate(["{name} wins!", "Your turn"], "es-MX")  # another order, a regional tag
    assert again.texts == first.texts
    assert len(fake.calls) == 1  # Google was asked once
    (stored,) = [t for t in store.list_translations("es") if t.source == "Your turn"]
    assert (stored.provider, stored.reviewed, stored.source_hash) == ("google", False, text_hash("Your turn"))
    service.translate(["Your turn"], "fr")
    assert len(fake.calls) == 2  # another language is another translation


def test_only_the_new_texts_are_sent() -> None:
    fake = FakeTranslator()
    service, _ = make_service(fake)
    service.translate(["a", "b"], "de")
    service.translate(["b", "c", "d"], "de")
    assert [texts for texts, _ in fake.calls] == [["a", "b"], ["c", "d"]]


def test_a_translation_that_loses_a_placeholder_is_refused(caplog: pytest.LogCaptureFixture) -> None:
    class Sloppy(FakeTranslator):
        def translate(self, texts: Any, target: str) -> list[str]:
            return ["¡Gana!" if "{name}" in text else f"[{target}] {text}" for text in texts]

    service, store = make_service(Sloppy())
    result = service.translate(["{name} wins!", "Your turn"], "es")
    assert result.status == "partial"
    assert result.texts == {"Your turn": "[es] Your turn"}
    assert [t.source for t in store.list_translations("es")] == ["Your turn"]
    (rejected,) = events(caplog, "translation_rejected")
    assert rejected["level"] == "WARNING" and rejected["source"] == "{name} wins!"


def test_when_the_translator_fails_the_originals_are_shown_and_one_alert_is_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    observability_monitor = translation.observability.Monitor()
    translation.observability.set_current(observability_monitor)
    now = {"t": 1000.0}
    fake = FakeTranslator(fail=TranslationError("Google answered 503: unavailable"))
    service = TranslationService(InMemoryStore(), fake, clock=FakeClock(), monotonic=lambda: now["t"])
    for _ in range(3):
        assert service.translate(["Your turn"], "es").status == "unavailable"
    assert service._store.list_translations() == []  # nothing remembered from a failure
    failures = events(caplog, "translation_failed")
    assert [f["alert"] for f in failures] == ["minor"]  # one failure, one alert; the rest wait it out
    assert failures[0]["level"] == "WARNING" and "503" in failures[0]["error"]
    # still down a minute later: logged again, but the same alert is not raised twice
    now["t"] += 61
    service.translate(["Your turn"], "es")
    assert [f["alert"] for f in events(caplog, "translation_failed")] == ["minor", None]


def test_after_a_failure_the_translator_is_left_alone_for_a_while() -> None:
    now = {"t": 1000.0}
    service = TranslationService(
        InMemoryStore(), FakeTranslator(fail=TranslationError("down")), clock=FakeClock(),
        retry_after_failure_seconds=60, monotonic=lambda: now["t"],
    )
    fake = service._translator
    for _ in range(5):
        assert service.translate(["Your turn"], "es").status == "unavailable"
    assert len(fake.calls) == 1  # not once per request
    now["t"] += 61
    service.translate(["Your turn"], "es")
    assert len(fake.calls) == 2  # tried again


def test_a_service_with_no_translator_shows_the_originals() -> None:
    service, _ = make_service(None)
    assert not service.enabled
    assert service.translate(["Your turn"], "es").status == "unavailable"


def test_the_daily_character_budget_caps_the_cost(caplog: pytest.LogCaptureFixture) -> None:
    translation.observability.set_current(translation.observability.Monitor())
    clock = {"now": dt.datetime(2026, 11, 1, 12, tzinfo=dt.timezone.utc)}
    fake = FakeTranslator()
    service, _ = make_service(fake, limit=20, clock=lambda: clock["now"])
    assert service.translate(["0123456789"], "es").status == "translated"  # 10 of 20
    assert service.translate(["0123456789", "abcdefghij", "klmnopqrst"], "es").status == "partial"  # 20 more: over
    (exceeded,) = events(caplog, "translation_budget_exceeded")
    assert (exceeded["alert"], exceeded["level"], exceeded["daily_character_limit"]) == ("minor", "ERROR", 20)
    assert len(fake.calls) == 1
    clock["now"] += dt.timedelta(days=1)  # a new day, a new budget
    assert service.translate(["abcdefghij"], "es").status == "translated"


def test_two_players_asking_for_the_same_new_text_cost_one_translation() -> None:
    class Slow(FakeTranslator):
        def translate(self, texts: Any, target: str) -> list[str]:
            time.sleep(0.2)
            return super().translate(texts, target)

    slow = Slow()
    service, _ = make_service(slow)
    results: list[Any] = []
    threads = [threading.Thread(target=lambda: results.append(service.translate(["Your turn"], "es"))) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(slow.calls) == 1 and all(r.status == "translated" for r in results)


def test_translations_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    store = JsonFileStore(path)
    TranslationService(store, FakeTranslator(), clock=FakeClock()).translate(["Your turn"], "es")
    store.close()
    reopened = JsonFileStore(path)
    try:
        (item,) = reopened.list_translations()
        assert (item.locale, item.source, item.text) == ("es", "Your turn", "[es] Your turn")
    finally:
        reopened.close()


# A game's string table


def test_the_string_table_is_read_from_the_games_code() -> None:
    assert game_text.extract_strings(GAME) == {"your_turn": "Your turn", "wins": "{name} wins!", "moves": "Moves: {count}"}
    assert game_text.extract_strings("<html>no table</html>") is None
    reversed_attributes = '<script id="i18n-strings" type="application/json">{"a": "A"}</script>'
    assert game_text.extract_strings(reversed_attributes) == {"a": "A"}


@pytest.mark.parametrize(
    ("block", "problem"),
    [
        ("{not json", "not valid JSON"),
        ('["a"]', "must be a JSON object"),
        ('{"a": 5}', "must be a string"),
        ('{"bad key": "x"}', "must be letters"),
        ('{"a": "' + "x" * 1001 + '"}', "the most is 1000"),
        (json.dumps({f"k{i}": "x" for i in range(401)}), "the most is 400"),
    ],
)
def test_a_broken_string_table_is_explained(block: str, problem: str) -> None:
    code = f'<script type="application/json" id="i18n-strings">{block}</script>'
    with pytest.raises(game_text.StringsError, match=problem):
        game_text.extract_strings(code)
    assert problem in game_text.lint(code)[0]


def test_a_well_made_game_has_no_warnings() -> None:
    assert game_text.lint(GAME) == []


def test_the_checker_finds_what_makes_a_game_hard_to_translate() -> None:
    assert "no i18n-strings block" in game_text.lint("<html><script>x.textContent = 'Hi there';</script></html>")[0]
    messy = """<script type="application/json" id="i18n-strings">{"a": "A <b>bold</b>", "unused": "U"}</script>
<script>
status.textContent = "Waiting for the other player";
status.textContent = name + " wins the game";
other.textContent = t("a");
ctx.fillText("Score", 1, 2);
t("missing");
</script>"""
    warnings = game_text.lint(messy)
    text = "\n".join(warnings)
    assert "t('missing') but the strings block has no 'missing'" in text
    assert "has 'unused', which the code never shows" in text
    assert "text written in the code instead of the strings block: 'Waiting for the other player'" in text
    assert "1 sentence(s) are built by joining pieces" in text
    assert "contains markup" in text
    assert "draws text on a canvas" in text


def test_the_helper_games_include_is_the_one_the_agent_is_told_about() -> None:
    assert "function t(key, params)" in game_text.HELPER_JS and "function applyLocale(message)" in game_text.HELPER_JS
    assert game_text.HELPER_JS in agent.SYSTEM_PROMPT
    assert agent.SYSTEM_PROMPT.index("TRANSLATION.") < agent.SYSTEM_PROMPT.index("RESPONSE FORMAT:")


def test_the_helper_cannot_end_the_games_script_block_early() -> None:
    """A game includes the helper inside <script>: a closing tag in it would break the page."""
    assert "<" not in game_text.HELPER_JS


def test_the_instructions_to_the_agent_describe_a_game_the_checker_accepts() -> None:
    example = game_text.PROMPT_RULES.split('<script type="application/json"', 1)[1]
    code = '<script type="application/json"' + example.split("- Call applyLocale", 1)[0]
    table = game_text.extract_strings(code)
    assert table == {"your_turn": "Your turn", "wins": "{name} wins!", "moves": "Moves: {count}"}


# The endpoints


class TranslatingApi(Api):
    def __init__(self, translator: Any = None) -> None:
        super().__init__()
        platform = self.client.app.state.platform  # type: ignore[attr-defined]
        platform._translation = TranslationService(platform._store, translator, clock=platform._clock)


def publish(api: TranslatingApi, owner: str, *, code: str = GAME, name: str = "Tic-tac-toe") -> str:
    game = api.ok("POST", "/games", as_user=owner, json={
        "name": name, "description": "Three in a row wins", "allowed_player_counts": [2],
        "allows_leave_mid_match": False, "allows_join_mid_match": False, "code": code,
    })
    return game["id"]


def test_a_games_texts_come_back_in_the_viewers_language() -> None:
    api = TranslatingApi(FakeTranslator())
    owner = api.new_user("owner")
    game_id = publish(api, owner)
    body = api.ok("GET", f"/games/{game_id}/versions/1/strings", params={"lang": "es"})
    assert (body["locale"], body["direction"], body["status"], body["translatable"]) == ("es", "ltr", "translated", True)
    assert body["name"] == "[es] Tic-tac-toe" and body["description"] == "[es] Three in a row wins"
    assert body["strings"] == {"your_turn": "[es] Your turn", "wins": "[es] {name} wins!", "moves": "[es] Moves: {count}"}
    assert body["warnings"] == []
    # the same endpoint in Arabic is right to left; the language follows Accept-Language too
    arabic = api.client.get(f"/games/{game_id}/versions/1/strings", headers={"Accept-Language": "ar"}).json()
    assert (arabic["locale"], arabic["direction"], arabic["name"]) == ("ar", "rtl", "[ar] Tic-tac-toe")


def test_english_is_not_translated() -> None:
    fake = FakeTranslator()
    api = TranslatingApi(fake)
    game_id = publish(api, api.new_user("owner"))
    body = api.ok("GET", f"/games/{game_id}/versions/1/strings")
    assert (body["locale"], body["status"], body["name"], body["strings"]["wins"]) == ("en", "source", "Tic-tac-toe", "{name} wins!")
    assert fake.calls == []


def test_without_a_translator_a_game_still_shows_its_own_texts() -> None:
    api = TranslatingApi(None)
    game_id = publish(api, api.new_user("owner"))
    body = api.ok("GET", f"/games/{game_id}/versions/1/strings", params={"lang": "fr"})
    assert (body["status"], body["name"], body["strings"]["your_turn"]) == ("unavailable", "Tic-tac-toe", "Your turn")


def test_a_failing_translator_does_not_fail_the_request() -> None:
    api = TranslatingApi(FakeTranslator(fail=TranslationError("down")))
    game_id = publish(api, api.new_user("owner"))
    response = api.client.get(f"/games/{game_id}/versions/1/strings", params={"lang": "de"})
    assert response.status_code == 200 and response.json()["status"] == "unavailable"
    assert response.json()["name"] == "Tic-tac-toe"


def test_a_game_without_a_string_table_translates_only_its_name_and_description() -> None:
    api = TranslatingApi(FakeTranslator())
    game_id = publish(api, api.new_user("owner"), code="<html><script>x.textContent='Hello there'</script></html>")
    body = api.ok("GET", f"/games/{game_id}/versions/1/strings", params={"lang": "es"})
    assert (body["translatable"], body["strings"], body["name"]) == (False, {}, "[es] Tic-tac-toe")
    assert "no i18n-strings block" in body["warnings"][0]


def test_an_unpublished_version_is_only_for_its_owner_and_players() -> None:
    api = TranslatingApi(FakeTranslator())
    owner, stranger = api.new_user("owner"), api.new_user("stranger")
    game_id = publish(api, owner)
    api.ok("POST", f"/games/{game_id}/unpublish", as_user=owner)
    assert api.request("GET", f"/games/{game_id}/versions/1/strings", params={"lang": "es"}).status_code == 404
    assert api.request("GET", f"/games/{game_id}/versions/1/strings", as_user=stranger, params={"lang": "es"}).status_code == 404
    assert api.request("GET", f"/games/{game_id}/versions/1/strings", as_user=owner, params={"lang": "es"}).status_code == 200


def test_the_names_and_descriptions_of_every_game_can_be_translated_at_once() -> None:
    fake = FakeTranslator()
    api = TranslatingApi(fake)
    owner = api.new_user("owner")
    first = publish(api, owner, name="Chess")
    second = publish(api, owner, name="Checkers")
    body = api.ok("GET", "/i18n/games/es")
    assert (body["locale"], body["status"]) == ("es", "translated")
    assert body["games"][first] == {"name": "[es] Chess", "description": "[es] Three in a row wins"}
    assert body["games"][second]["name"] == "[es] Checkers"
    assert len(fake.calls) == 1 and len(fake.calls[0][0]) == 3  # one request; the shared description counted once
    api.ok("GET", "/i18n/games/es")
    assert len(fake.calls) == 1  # remembered
    assert api.ok("GET", "/i18n/games/en")["status"] == "source"
    assert api.request("GET", "/i18n/games/xx").status_code == 404


def test_an_administrator_can_review_correct_and_forget_translations() -> None:
    fake = FakeTranslator()
    api = TranslatingApi(fake)
    game_id = publish(api, api.new_user("owner"))
    api.ok("GET", f"/games/{game_id}/versions/1/strings", params={"lang": "es"})
    listed = api.ok("GET", "/admin/translations", params={"locale": "es"})
    assert {item["source"] for item in listed} == {"Tic-tac-toe", "Three in a row wins", "Your turn", "{name} wins!", "Moves: {count}"}
    assert all(item["provider"] == "google" and not item["reviewed"] for item in listed)

    fixed = api.ok("PUT", "/admin/translations", json={"locale": "es", "source": "{name} wins!", "text": "¡{name} gana!"})
    assert (fixed["provider"], fixed["reviewed"]) == ("manual", True)
    body = api.ok("GET", f"/games/{game_id}/versions/1/strings", params={"lang": "es"})
    assert body["strings"]["wins"] == "¡{name} gana!"  # the correction is what is served
    assert len(fake.calls) == 1  # and nothing was translated again

    api.ok("DELETE", f"/admin/translations/es/{text_hash('{name} wins!')}")
    again = api.ok("GET", f"/games/{game_id}/versions/1/strings", params={"lang": "es"})
    assert again["strings"]["wins"] == "[es] {name} wins!"  # machine-translated afresh
    assert len(fake.calls) == 2


def test_a_correction_must_be_for_a_translated_language_and_keep_the_placeholders() -> None:
    api = TranslatingApi(FakeTranslator())
    for body in [
        {"locale": "es", "source": "{name} wins!", "text": "¡Gana!"},
        {"locale": "es", "source": "Hi", "text": "Hola {extra}"},
        {"locale": "en", "source": "Hi", "text": "Hello"},
    ]:
        assert api.request("PUT", "/admin/translations", json=body).status_code == 400
    assert api.request("PUT", "/admin/translations", json={"locale": "ja", "source": "Hi", "text": "x"}).status_code == 422


def test_the_translation_admin_needs_the_master_password() -> None:
    from fastapi.testclient import TestClient

    from game_platform.api import create_app
    from game_platform.service import GamePlatform

    client = TestClient(create_app(GamePlatform(), master_password="s3cret"))
    assert client.get("/admin/translations").status_code == 401
    assert client.put("/admin/translations", json={}).status_code == 401
    assert client.delete("/admin/translations/es/abc").status_code == 401
    assert client.get("/admin/translations", auth=("", "s3cret")).status_code == 200
