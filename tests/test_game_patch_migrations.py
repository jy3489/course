"""Startup patches use the real versioning/publish path with guarded originals."""

import datetime as dt
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from game_platform import game_patch_migrations as migrations
from game_platform.models import Game, GameRules
from game_platform.service import GamePlatform
from game_platform.store import InMemoryStore


def _seed_versions(store, game):
    for version in range(1, game.version + 1):
        store.add_game_version(replace(game, version=version))


@pytest.fixture
def patch_case(tmp_path, monkeypatch):
    original_client = "<p>original</p>"
    server = (Path(__file__).parents[1] / "games" / "tictactoe.server.js").read_text()
    desired_client = "<p>reviewed client</p>"
    desired_server = server + "\n// reviewed migration\n"
    (tmp_path / "client.html").write_text(desired_client)
    (tmp_path / "server.js").write_text(desired_server)
    entry = {
        "id": "reviewed-game",
        "name": "Reviewed manifest name",
        "expected_version": 9,
        "client": "client.html",
        "server": "server.js",
        "original_client_sha256": hashlib.sha256(original_client.encode()).hexdigest(),
        "original_server_sha256": hashlib.sha256(server.encode()).hexdigest(),
    }
    (tmp_path / "manifest.json").write_text(json.dumps([entry]))
    monkeypatch.setattr(migrations, "BATCH1_DIRECTORY", tmp_path)
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    game = Game(
        id=entry["id"], version=9, owner_user_id="real-author", name="Author title",
        description="Author description", code=original_client, server_code=server,
        rules=GameRules(allowed_player_counts=(2,), allows_leave_mid_match=False,
                        allows_join_mid_match=False),
        created_at=now, updated_at=now,
    )
    store = InMemoryStore()
    _seed_versions(store, game)
    platform = GamePlatform(store=store)
    return platform, store, game, desired_client, desired_server, tmp_path, entry


def test_updates_through_publish_gate_and_is_idempotent(patch_case, monkeypatch):
    platform, store, original, client, server, _, _ = patch_case
    gate = platform._gate_server_code
    calls = []

    def checked_gate(*args):
        calls.append(args)
        return gate(*args)

    monkeypatch.setattr(platform, "_gate_server_code", checked_gate)
    assert migrations.apply_audit_batch1(platform)[0]["status"] == "updated"
    updated = platform.get_game(original.id)
    assert updated.version == 10
    assert (updated.code, updated.server_code) == (client, server)
    assert (updated.name, updated.description, updated.owner_user_id, updated.rules) == (
        original.name, original.description, original.owner_user_id, original.rules
    )
    assert store.get_game_version(original.id, 9) == original
    assert len(calls) == 1
    assert migrations.apply_audit_batch1(platform)[0]["status"] == "unchanged"
    assert platform.get_game(original.id) == updated
    assert len(calls) == 1


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"version": 10}, "skipped_version"),
        ({"code": "author changed client"}, "skipped_hash"),
        ({"server_code": "author changed server"}, "skipped_hash"),
        ({"published": False}, "skipped_missing_or_unpublished"),
        ({"deleted": True}, "skipped_deleted"),
    ],
)
def test_preserves_author_edits_and_visibility(patch_case, monkeypatch, changes, status):
    platform, store, original, _, _, _, _ = patch_case
    current = replace(original, **changes)
    if current.version > original.version:
        store.add_game_version(current)
    else:
        store.put_game_version(current)
    if not current.published:
        store.set_game_published(current.id, False)
    if current.deleted:
        store.delete_game(current.id)

    def must_not_update(**kwargs):
        pytest.fail("guarded game must not be updated")

    monkeypatch.setattr(platform, "update_game", must_not_update)
    assert migrations.apply_audit_batch1(platform)[0]["status"] == status
    assert store.get_game(original.id) == current


def test_missing_game_is_skipped(patch_case):
    platform = GamePlatform(store=InMemoryStore())
    assert migrations.apply_audit_batch1(platform)[0]["status"] == "skipped_missing_or_unpublished"


def test_gate_rejection_keeps_both_code_parts_and_continues(patch_case):
    platform, store, original, _, _, directory, entry = patch_case
    following = replace(original, id="following-game")
    _seed_versions(store, following)
    (directory / "bad-server.js").write_text("const game = {newMatch(){ throw new Error('rejected candidate'); }};")
    (directory / "manifest.json").write_text(json.dumps([
        {**entry, "server": "bad-server.js"}, {**entry, "id": following.id}
    ]))
    results = migrations.apply_audit_batch1(platform)
    assert [r["status"] for r in results] == ["error", "updated"]
    assert "failed its tests" in results[0]["detail"]
    assert store.get_game(original.id) == original
    assert store.get_game_version(original.id, 10) is None
    assert store.get_game(following.id).version == 10


def test_already_desired_code_is_unchanged_even_after_later_version(patch_case):
    platform, store, original, client, server, _, _ = patch_case
    current = replace(original, version=15, code=client, server_code=server)
    for version in range(original.version + 1, current.version + 1):
        store.add_game_version(replace(current, version=version))
    assert migrations.apply_audit_batch1(platform)[0]["status"] == "unchanged"
    assert store.get_game(original.id) == current
