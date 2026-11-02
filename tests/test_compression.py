"""Response compression and static-file caching.

Responses are gzipped on their way out (clients that ask for it get far
fewer bytes for the big payloads: game lists, match lists, the portal
script), and static files referenced with a ?v= version may be cached
immutably — the URL is the version, so a changed file is a new URL.
Unversioned pages and scripts keep revalidating on every load.
"""

from game_platform.tests.helpers import Api

GZIP = {"Accept-Encoding": "gzip"}
IDENTITY = {"Accept-Encoding": "identity"}


def test_json_responses_are_gzipped_when_the_client_asks(api: Api) -> None:
    owner = api.new_user("owner")
    api.new_game(owner=owner)

    plain = api.client.get("/games", headers=IDENTITY)
    zipped = api.client.get("/games", headers=GZIP)
    assert "content-encoding" not in plain.headers
    assert zipped.headers["content-encoding"] == "gzip"
    # The decoded body is the same payload either way.
    assert zipped.json() == plain.json()


def test_no_compression_without_a_gzip_offer(api: Api) -> None:
    response = api.client.get("/static/portal.js", headers=IDENTITY)
    assert response.status_code == 200
    assert "content-encoding" not in response.headers


def test_the_portal_script_is_gzipped(api: Api) -> None:
    response = api.client.get("/static/portal.js", headers=GZIP)
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert len(response.content) > 10_000  # decoded back to the full script


def test_versioned_static_files_are_cached_immutably(api: Api) -> None:
    response = api.client.get("/static/portal.js?v=12345")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_unversioned_static_files_still_revalidate(api: Api) -> None:
    response = api.client.get("/static/portal.js")
    assert response.status_code == 200
    assert "immutable" not in response.headers.get("cache-control", "")


def test_the_portal_page_still_revalidates(api: Api) -> None:
    response = api.client.get("/portal")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
