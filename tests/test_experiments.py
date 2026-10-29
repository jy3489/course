import datetime as dt

from game_platform.models import GameRules
from fastapi.testclient import TestClient

from game_platform.api import create_app
from game_platform.json_file_store import JsonFileStore
from game_platform.service import GamePlatform
from game_platform.tests.helpers import Api


def test_skin_assignment_is_stable_and_hashes_across_variants() -> None:
    api = Api()
    user_id = api.new_user("alice")

    first = api.ok("GET", "/users/me/experiments/skin", as_user=user_id)
    assert api.ok("GET", "/users/me/experiments/skin", as_user=user_id) == first

    platform = api.client.app.state.platform
    assignments = {
        platform.assign_skin(user_id=f"synthetic-user-{index}").key
        for index in range(100)
    }
    assert assignments == {"classic", "aurora", "sunset"}


def test_signup_experiment_events_do_not_consume_generated_ids() -> None:
    ids = iter(("user-1", "game-1"))
    platform = GamePlatform(new_id=lambda: next(ids))
    user = platform.create_user(display_name="alice")

    game = platform.create_game(
        caller_id=user.id,
        name="game",
        description="",
        rules=GameRules(
            allowed_player_counts=(1,),
            allows_leave_mid_match=False,
            allows_join_mid_match=False,
        ),
        code="",
    )

    assert game.id == "game-1"


def test_match_start_tracking_uses_match_timestamp_without_advancing_clock() -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    clock_reads = 0

    def clock() -> dt.datetime:
        nonlocal clock_reads, now
        clock_reads += 1
        now += dt.timedelta(minutes=1)
        return now

    ids = iter(("user-1", "game-1", "match-1"))
    platform = GamePlatform(clock=clock, new_id=lambda: next(ids))
    user = platform.create_user(display_name="alice")
    game = platform.create_game(
        caller_id=user.id,
        name="game",
        description="",
        rules=GameRules(
            allowed_player_counts=(1,),
            allows_leave_mid_match=False,
            allows_join_mid_match=False,
        ),
        code="",
    )
    match = platform.create_match(
        caller_id=user.id, game_id=game.id, num_computer_opponents=0
    )
    started = platform.start_match(
        caller_id=user.id,
        match_id=match.id,
        first_turn_player_indices=frozenset({0}),
        initial_state={},
    )
    match_start_event = next(
        event
        for event in platform._store.list_experiment_events()
        if event.kind == "match_start"
    )

    assert clock_reads == 6
    assert match_start_event.occurred_at == started.updated_at


def test_skin_assignment_merge_keeps_survivor_then_inherits_source() -> None:
    api = Api()
    target = api.new_user("target")
    source = api.new_user("source")
    platform = api.client.app.state.platform
    target_variant = platform.assign_skin(user_id=target)
    source_variant = platform.assign_skin(user_id=source)
    assert target_variant is not None and source_variant is not None

    api.ok(
        "POST",
        f"/users/{source}/merge",
        as_user=source,
        json={"into_user_id": target},
    )
    assert (
        api.ok("GET", "/users/me/experiments/skin", as_user=source)["key"]
        == target_variant.key
    )

    inherited_source = api.new_user("inherited source")
    inherited_target = api.new_user("inherited target")
    inherited_variant = platform.assign_skin(user_id=inherited_source)
    platform._store.delete_experiment_assignment(
        user_id=inherited_target, experiment_id="ui-skins"
    )
    api.ok(
        "POST",
        f"/users/{inherited_source}/merge",
        as_user=inherited_source,
        json={"into_user_id": inherited_target},
    )
    assert (
        api.ok("GET", "/users/me/experiments/skin", as_user=inherited_source)["key"]
        == inherited_variant.key
    )


def test_campaign_funnel_and_seven_day_returning_users() -> None:
    api = Api()
    click = api.client.get("/c/students-a", follow_redirects=False)
    assert click.status_code == 302
    assert click.headers["location"] == "/portal?campaign=students-a"

    signup = api.client.post(
        "/users",
        headers={"X-Campaign-Code": "students-a"},
        json={"display_name": "campaign user"},
    )
    assert signup.status_code == 201
    user_id = signup.json()["id"]
    api.passwords_by_user_id[user_id] = signup.json()["password"]

    game_id = api.new_game(owner=user_id)
    api.new_match(
        owner=user_id,
        game_id=game_id,
        num_computer_opponents=1,
        start=True,
    )
    account = api.client.app.state.platform._store.get_user(user_id)
    assert account is not None
    api.client.app.state.platform._clock = lambda: (
        account.created_at + dt.timedelta(days=8)
    )
    api.new_match(
        owner=user_id,
        game_id=game_id,
        num_computer_opponents=1,
        start=True,
    )

    stats = api.ok("GET", "/admin/experiments")
    campaign = next(
        item
        for item in stats["campaign_experiment"]["variants"]
        if item["key"] == "students-a"
    )
    assert campaign["clicks"] == 1
    assert campaign["signups"] == 1
    assert campaign["match_starts"] == 2
    assert campaign["users_started_match"] == 1
    assert campaign["returning_active_users"] == 1
    assert campaign["signup_rate"] == 100
    assert campaign["return_rate"] == 100


def test_admin_can_create_campaign_and_skin_variants() -> None:
    api = Api()
    campaign = api.ok(
        "POST",
        "/admin/experiments/campaign-links",
        json={"name": "Student D", "key": "students-d"},
    )
    assert campaign["url"].endswith("/c/students-d")
    assert campaign["budget_usd"] == 100
    spent = api.ok(
        "PATCH",
        f"/admin/experiments/campaign-links/{campaign['variant_id']}",
        json={"spend_usd": 73.5},
    )
    assert spent["spend_usd"] == 73.5
    assert api.request(
        "PATCH",
        f"/admin/experiments/campaign-links/{campaign['variant_id']}",
        json={"spend_usd": 101},
    ).status_code == 422
    skin = api.ok(
        "POST",
        "/admin/experiments/skin-variants",
        json={"name": "Student skin", "key": "student-skin"},
    )
    assert skin["key"] == "student-skin"


def test_experiment_records_survive_json_store_restart(tmp_path) -> None:
    path = tmp_path / "data.json"
    store = JsonFileStore(path)
    api = Api(store=store)
    api.client.get("/c/students-b")
    signup = api.client.post(
        "/users",
        headers={"X-Campaign-Code": "students-b"},
        json={"display_name": "persisted user"},
    )
    assert signup.status_code == 201
    store.close()

    restarted = JsonFileStore(path)
    stats = Api(store=restarted).ok("GET", "/admin/experiments")
    restarted.close()
    campaign = next(
        item
        for item in stats["campaign_experiment"]["variants"]
        if item["key"] == "students-b"
    )
    assert campaign["clicks"] == 1
    assert campaign["signups"] == 1


def test_deleting_a_user_removes_user_level_experiment_data() -> None:
    api = Api()
    signup = api.client.post(
        "/users",
        headers={"X-Campaign-Code": "students-c"},
        json={"display_name": "deleted user"},
    )
    user_id = signup.json()["id"]
    before = api.ok("GET", "/admin/experiments")
    assert next(
        item
        for item in before["campaign_experiment"]["variants"]
        if item["key"] == "students-c"
    )["signups"] == 1

    api.ok("DELETE", f"/admin/users/{user_id}")
    after = api.ok("GET", "/admin/experiments")
    assert next(
        item
        for item in after["campaign_experiment"]["variants"]
        if item["key"] == "students-c"
    )["signups"] == 0


def test_experiment_admin_endpoints_require_master_password() -> None:
    client = TestClient(create_app(GamePlatform(), master_password="secret"))
    assert client.get("/admin/experiments").status_code == 401
    assert client.get(
        "/admin/experiments", auth=("admin", "secret")
    ).status_code == 200
    assert client.post(
        "/admin/experiments/campaign-links",
        auth=("admin", "secret"),
        json={"name": "New campaign", "key": "new-campaign"},
    ).status_code == 201
