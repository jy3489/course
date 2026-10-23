"""Languages: catalogs, negotiation, localized errors, the /i18n API and user locale."""

import ast
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from game_platform import i18n
from game_platform.api import create_app
from game_platform.tests.helpers import Api

PLACEHOLDER = re.compile(r"\{(\w+)\}")
PACKAGE_DIR = Path(__file__).parent.parent


def _catalog(code: str) -> dict[str, str]:
    return json.loads((i18n.LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))


def _get(api: Api, path: str, **headers: str):
    return api.client.get(path, headers={k.replace("_", "-"): v for k, v in headers.items()})


# Catalogs


def test_every_supported_language_has_a_catalog_and_nothing_else_does() -> None:
    files = {path.stem for path in i18n.LOCALES_DIR.glob("*.json")}
    assert files == set(i18n.LOCALE_CODES)


@pytest.mark.parametrize("code", i18n.LOCALE_CODES)
def test_a_catalog_has_exactly_the_english_keys_and_placeholders(code: str) -> None:
    english, catalog = _catalog("en"), _catalog(code)
    assert set(catalog) == set(english), (
        f"missing: {sorted(set(english) - set(catalog))}, "
        f"extra: {sorted(set(catalog) - set(english))}"
    )
    for key, text in catalog.items():
        assert text.strip(), f"{code}:{key} is empty"
        assert set(PLACEHOLDER.findall(text)) == set(
            PLACEHOLDER.findall(english[key])
        ), f"{code}:{key} has different placeholders than English"


def test_only_arabic_is_right_to_left() -> None:
    assert {l.code for l in i18n.LOCALES if l.direction == "rtl"} == {"ar"}


def _error_messages_in_the_code() -> list[tuple[str, int, str]]:
    """Every literal message of an error that reaches API clients, with placeholders
    for f-string values."""
    client_errors = {
        "UnauthorizedError",
        "ForbiddenError",
        "NotFoundError",
        "InvalidRequestError",
        "ConflictError",
    }

    def text_of(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                part.value if isinstance(part, ast.Constant) else "0"
                for part in node.values
            )
        return None

    found = []
    for name in ("service.py", "api.py"):
        tree = ast.parse((PACKAGE_DIR / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
                continue
            call = node.exc
            func = getattr(call.func, "id", getattr(call.func, "attr", ""))
            message = None
            if func in client_errors and call.args:
                message = text_of(call.args[0])
            elif func == "HTTPException":
                message = next(
                    (text_of(kw.value) for kw in call.keywords if kw.arg == "detail"),
                    None,
                )
            if message is not None:
                found.append((name, node.lineno, message))
    return found


def test_every_error_message_in_the_code_has_a_catalog_entry() -> None:
    """When this fails, add an `error.*` entry to every locales/*.json file for the new
    message (the English text must match what the code raises)."""
    messages = _error_messages_in_the_code()
    assert len(messages) > 30  # the scan found the errors
    untranslated = [
        f"{name}:{line} {message!r}"
        for name, line, message in messages
        if i18n.localize_error(message, "es")[1] == i18n.UNKNOWN_ERROR_CODE
    ]
    assert untranslated == []


# Negotiation


@pytest.mark.parametrize(
    ("tag", "code"),
    [
        ("es", "es"),
        ("ES", "es"),
        ("es-MX", "es"),
        ("pt_BR", "pt"),
        ("zh-Hant-TW", "zh"),
        ("ar-EG", "ar"),
        ("ja", None),
        ("", None),
        (None, None),
    ],
)
def test_language_tags_map_to_supported_languages(tag: str | None, code: str | None) -> None:
    assert i18n.normalize_locale(tag) == code


def test_accept_language_is_ordered_by_quality() -> None:
    assert i18n.parse_accept_language("fr;q=0.5, de, en;q=0.8, *;q=0.1, xx;q=0") == [
        "de",
        "en",
        "fr",
    ]
    assert i18n.parse_accept_language(None) == []
    assert i18n.parse_accept_language("de;q=oops") == []


def test_the_response_language_prefers_explicit_then_saved_then_accept_language() -> None:
    def pick(**kwargs: str) -> str:
        return i18n.negotiate(**kwargs)

    assert pick(explicit="fr", saved="de", accept_language="es") == "fr"
    assert pick(explicit="xx", saved="de", accept_language="es") == "de"
    assert pick(saved="", accept_language="ja, pt-BR;q=0.7") == "pt"
    assert pick(accept_language="ja") == "en"
    assert pick() == "en"


# Translating


def test_translate_fills_placeholders_and_falls_back_to_english() -> None:
    assert i18n.translate("ui.match.moves_count", "de", count=3) == "Züge: 3"
    assert i18n.translate("ui.match.moves_count", "en", count=3) == "Moves: 3"
    assert i18n.translate("no.such.key", "fr") == "no.such.key"
    assert i18n.translate("ui.match.waiting_for", "es") == "Esperando a {name}"


def test_errors_are_translated_by_matching_their_english_text() -> None:
    assert i18n.localize_error("game not found", "es") == (
        "juego no encontrado",
        "game_not_found",
        {},
    )
    text, code, params = i18n.localize_error("expected 5 moves but the match has 2", "de")
    assert (text, code, params) == (
        "5 Züge erwartet, aber die Partie hat 2",
        "expected_moves",
        {"expected": "5", "actual": "2"},
    )
    # English is never rewritten, and unknown messages keep their text.
    assert i18n.localize_error("game not found", "en")[0] == "game not found"
    assert i18n.localize_error("something new", "fr") == (
        "something new",
        i18n.UNKNOWN_ERROR_CODE,
        {},
    )


# The /i18n API


def test_the_supported_languages_are_listed(api: Api) -> None:
    body = api.ok("GET", "/i18n/locales")
    assert body["default"] == "en"
    assert [locale["code"] for locale in body["locales"]] == list(i18n.LOCALE_CODES)
    arabic = next(locale for locale in body["locales"] if locale["code"] == "ar")
    assert arabic == {
        "code": "ar",
        "name": "العربية",
        "english_name": "Arabic",
        "direction": "rtl",
    }


def test_a_catalog_has_every_text_in_one_language(api: Api) -> None:
    body = api.ok("GET", "/i18n/es")
    assert (body["locale"], body["direction"]) == ("es", "ltr")
    assert body["messages"]["ui.action.save"] == "Guardar"
    assert set(body["messages"]) == set(_catalog("en"))
    assert api.ok("GET", "/i18n/ar")["direction"] == "rtl"


def test_a_catalog_can_be_limited_to_a_prefix(api: Api) -> None:
    messages = api.ok("GET", "/i18n/fr", params={"prefix": "ui.action."})["messages"]
    assert messages and all(key.startswith("ui.action.") for key in messages)
    assert messages["ui.action.save"] == "Enregistrer"


def test_a_regional_tag_gets_its_language_catalog(api: Api) -> None:
    assert api.ok("GET", "/i18n/pt-BR")["locale"] == "pt"


def test_an_unsupported_language_is_a_404_in_the_callers_language(api: Api) -> None:
    response = _get(api, "/i18n/xx", accept_language="es")
    assert response.status_code == 404
    assert response.json() == {
        "detail": "idioma no admitido: xx",
        "code": "locale_not_supported",
        "params": {"locale": "xx"},
    }


def test_a_catalog_is_cacheable_with_an_etag(api: Api) -> None:
    first = api.client.get("/i18n/de")
    etag = first.headers["etag"]
    assert etag == f'"{first.json()["version"]}"'
    assert first.headers["cache-control"] == "no-cache"
    again = api.client.get("/i18n/de", headers={"If-None-Match": etag})
    assert (again.status_code, again.content) == (304, b"")
    # Another language, or another selection, is another version.
    assert api.client.get("/i18n/es").headers["etag"] != etag
    assert api.client.get("/i18n/de?prefix=ui.").headers["etag"] != etag


# Localized API responses


def test_errors_are_unchanged_for_english_and_gain_a_code(api: Api) -> None:
    response = api.client.get("/matches/nope")
    assert response.status_code == 404
    assert response.json() == {"detail": "match not found", "code": "match_not_found"}
    assert response.headers["content-language"] == "en"


def test_errors_follow_accept_language(api: Api) -> None:
    response = _get(api, "/matches/nope", accept_language="es-MX,es;q=0.9,en;q=0.5")
    assert response.json() == {"detail": "partida no encontrada", "code": "match_not_found"}
    assert response.headers["content-language"] == "es"
    assert "Accept-Language" in response.headers["vary"]


def test_the_lang_parameter_beats_accept_language(api: Api) -> None:
    response = api.client.get(
        "/matches/nope", params={"lang": "fr"}, headers={"Accept-Language": "es"}
    )
    assert response.json()["detail"] == "partie introuvable"


def test_a_users_saved_language_applies_to_their_requests(api: Api) -> None:
    user = api.ok("POST", "/users", json={"display_name": "hans", "locale": "de"})
    headers = {"X-User-Id": user["id"], "X-User-Password": user["password"]}
    saved = api.client.get("/matches/nope", headers=headers | {"Accept-Language": "fr"})
    assert saved.json()["detail"] == "Partie nicht gefunden"
    chosen = api.client.get(
        "/matches/nope", params={"lang": "zh"}, headers=headers | {"Accept-Language": "fr"}
    )
    assert chosen.json()["detail"] == "找不到该对局"


def test_errors_carry_the_values_they_mention(api: Api) -> None:
    alice, bob = api.new_user("alice"), api.new_user("bob")
    game_id = api.new_game(owner=alice)
    match_id = api.new_match(owner=alice, game_id=game_id, joiners=[bob], start=True)
    response = api.client.post(
        f"/matches/{match_id}/moves",
        headers={
            "X-User-Id": alice,
            "X-User-Password": api.passwords_by_user_id[alice],
            "Accept-Language": "fr",
        },
        json={"new_state": 1, "next_turn_player_indices": [1], "expected_move_count": 7},
    )
    assert response.status_code == 409
    assert response.json() == {
        "detail": "7 coups attendus mais la partie en compte 0",
        "code": "expected_moves",
        "params": {"expected": "7", "actual": "0"},
    }


def test_missing_credentials_are_reported_in_the_callers_language(api: Api) -> None:
    response = _get(api, "/matches", accept_language="zh")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"
    assert "X-User-Id" in response.json()["detail"]
    assert "缺少" in response.json()["detail"]


def test_unknown_routes_are_localized_too(api: Api) -> None:
    response = _get(api, "/no/such/route", accept_language="es")
    assert (response.status_code, response.json()) == (
        404,
        {"detail": "No encontrado", "code": "route_not_found"},
    )


def test_the_master_password_error_is_localized_and_keeps_its_challenge() -> None:
    client = TestClient(create_app(master_password="s3cret"))
    response = client.get("/browse", headers={"Accept-Language": "fr"})
    assert response.status_code == 401
    assert response.json()["detail"].startswith("le mot de passe maître")
    assert response.headers["www-authenticate"].startswith("Basic")


# A user's language


def test_a_user_is_created_with_a_language(api: Api) -> None:
    user = api.ok("POST", "/users", json={"display_name": "ana", "locale": "pt-BR"})
    assert user["locale"] == "pt"
    assert api.ok("GET", f"/users/{user['id']}")["locale"] == "pt"
    assert api.ok("POST", "/users", json={"display_name": "bob"})["locale"] == ""


def test_a_user_changes_and_clears_their_language(api: Api) -> None:
    ana = api.new_user("ana")
    assert api.ok("PATCH", f"/users/{ana}", as_user=ana, json={"locale": "es"})["locale"] == "es"
    # Changing only the name keeps the language, and the reverse.
    renamed = api.ok("PATCH", f"/users/{ana}", as_user=ana, json={"display_name": "Ana"})
    assert (renamed["display_name"], renamed["locale"]) == ("Ana", "es")
    both = api.ok(
        "PATCH", f"/users/{ana}", as_user=ana, json={"display_name": "A", "locale": "ar"}
    )
    assert (both["display_name"], both["locale"]) == ("A", "ar")
    assert api.ok("PATCH", f"/users/{ana}", as_user=ana, json={"locale": ""})["locale"] == ""


def test_an_unsupported_language_or_an_empty_update_is_rejected(api: Api) -> None:
    ana = api.new_user("ana")
    unsupported = api.request("PATCH", f"/users/{ana}", as_user=ana, json={"locale": "ja"})
    assert unsupported.status_code == 422
    assert api.request("POST", "/users", json={"display_name": "x", "locale": "ja"}).status_code == 422
    assert api.request("PATCH", f"/users/{ana}", as_user=ana, json={}).status_code == 422
    assert api.request("PATCH", f"/users/{ana}", as_user=ana, json={"locale": None}).status_code == 422


def test_users_cannot_change_each_others_language(api: Api) -> None:
    ana, bob = api.new_user("ana"), api.new_user("bob")
    response = api.request("PATCH", f"/users/{ana}", as_user=bob, json={"locale": "es"})
    assert response.status_code == 403


def test_the_statistics_count_users_by_language(api: Api) -> None:
    api.ok("POST", "/users", json={"display_name": "a", "locale": "es"})
    api.ok("POST", "/users", json={"display_name": "b", "locale": "es"})
    api.ok("POST", "/users", json={"display_name": "c"})
    assert api.ok("GET", "/admin/stats")["users"]["by_locale"] == {"es": 2, "unset": 1}


# The web pages (static/i18n.js)

STATIC_DIR = PACKAGE_DIR / "static"


def test_every_key_the_web_pages_use_is_in_the_catalog() -> None:
    """data-i18n attributes in the HTML and I18n.t("key") calls in the scripts."""
    english = _catalog("en")
    used: dict[str, set[str]] = {}
    for page in sorted(STATIC_DIR.glob("*.html")):
        for key in re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', page.read_text(encoding="utf-8")):
            used.setdefault(key, set()).add(page.name)
    for script in sorted(STATIC_DIR.glob("*.js")):
        for key in re.findall(r'I18n\.t\(\s*"([^"]+)"', script.read_text(encoding="utf-8")):
            used.setdefault(key, set()).add(script.name)
    assert len(used) > 100  # the scan found the keys
    missing = {key: sorted(files) for key, files in used.items() if key not in english}
    assert missing == {}


def test_a_pages_built_in_english_matches_the_english_catalog() -> None:
    """The HTML is written in English so a page works before (and without) its catalog;
    English readers see exactly the catalog's texts either way."""
    english = _catalog("en")
    mismatches = []
    for page in sorted(STATIC_DIR.glob("*.html")):
        text = page.read_text(encoding="utf-8")
        for key, shown in re.findall(r'data-i18n="([^"]+)"[^>]*>([^<]*)<', text):
            shown = re.sub(r"\s+", " ", shown).strip()
            if english.get(key) != shown:
                mismatches.append(f"{page.name}: {key} is {shown!r}, catalog says {english.get(key)!r}")
    assert mismatches == []


def test_pages_with_translated_text_load_the_language_script() -> None:
    for page in sorted(STATIC_DIR.glob("*.html")):
        text = page.read_text(encoding="utf-8")
        if "data-i18n" in text:
            assert "/static/i18n.js" in text, f"{page.name} has data-i18n but no i18n.js"
            # i18n.js must come before the scripts that call I18n.t
            assert text.index("/static/i18n.js") < text.index("/static/auth.js"), page.name
