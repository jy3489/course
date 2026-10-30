"""Reproducible historical activity through real service and QuickJS operations.

This is an offline workload, not a browser simulation. Each synthetic match and
its progress checkpoint commit together. Existing users are preserved on resume.
"""

import datetime as dt
import hashlib
import random
from dataclasses import replace

from game_platform.scaling_service import ScalablePlatform
from game_platform.scaling_tools import seed

LOGIC = """
const game = {
 newMatch(ctx) { return {state:{count:0},turn:[ctx.players[0].id]}; },
 applyMove(state,id,move,ctx) {
   if (!move || !Number.isInteger(move.add) || move.add<1 || move.add>3)
     throw new InvalidMove('add must be 1..3');
   const count=state.count+move.add;
   const next=ctx.players[(ctx.players.findIndex(p=>p.id===id)+1)%ctx.players.length].id;
   const result={state:{count},turn:count>=10?null:[next]};
   if (count>=10) {
     const humans=ctx.players.filter(p=>p.kind==='human');
     const winner=humans.find(p=>p.id===id)||humans[0];
     result.payouts=winner?{[winner.id]:ctx.pot}:{};
   }
   return result;
 },
 computerMove(state,id,ctx) { return {add:1+Math.floor(ctx.random()*3)}; },
 randomMove(state,id,ctx) { return this.computerMove(state,id,ctx); },
 viewFor(state,id,ctx) { return state; }
};
"""


def generate(store, *, run_id, users, games, matches, max_writes):
    if users < 4 or games < 1 or matches < 0:
        raise ValueError("activity needs at least four users and one game")
    estimate = users * 8 + games * 40 + matches * 400
    if estimate > max_writes:
        raise ValueError(f"estimated {estimate} writes exceeds budget {max_writes}")
    manifest = store.ref("seed_runs", "activity:" + run_id)
    settings = {"users": users, "games": games, "matches": matches}
    prior = store.raw_get(manifest) or {}
    if prior and prior["settings"] != settings:
        raise ValueError("activity run settings changed")
    prefix = "activity-" + run_id
    seed(
        store,
        run_id=prefix,
        users=users,
        games=games,
        matches=0,
        moves_per_match=0,
        max_writes=max_writes,
    )
    uid = lambda i: f"synthetic-{prefix}-user-{i}"
    gid = lambda i: f"synthetic-{prefix}-game-{i}"
    for i in range(games):
        game = store.get_game(gid(i))
        if not game.server_code:
            store.put_game_version(
                replace(
                    game,
                    server_code=LOGIC,
                    rules=replace(game.rules, allowed_player_counts=(1, 2, 3, 4)),
                )
            )
    base = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    for i in range(prior.get("done", 0), matches):
        rng = random.Random(f"{run_id}:{i}")
        now = [base + dt.timedelta(seconds=rng.randrange(30 * 86400))]
        mid = f"synthetic-{prefix}-played-{i}"
        platform = ScalablePlatform(
            store=store,
            clock=lambda: now[0],
            new_id=lambda: mid,
            new_invite_token=lambda: mid + "-invite",
        )
        people = rng.sample(range(users), rng.randint(1, 4))
        computers = rng.randrange(5 - len(people))
        # A complete service operation and checkpoint are one commit, so a
        # lost response or interrupted process cannot duplicate payouts.
        with store.atomic():
            match = platform.create_match(
                caller_id=uid(people[0]),
                game_id=gid(i % games),
                num_computer_opponents=computers,
                private=rng.random() < 0.25,
            )
            match = replace(
                match,
                seed=int.from_bytes(hashlib.sha256(mid.encode()).digest()[:4], "big"),
            )
            store.put_match(match)
            invite = (
                platform.create_invite(caller_id=uid(people[0]), match_id=mid)
                if match.private and len(people) > 1
                else None
            )
            for person in people[1:]:
                if match.private:
                    match = platform.accept_invite(
                        caller_id=uid(person), token=invite.token
                    )
                else:
                    match = platform.join_match(caller_id=uid(person), match_id=mid)
            if rng.random() >= 0.15:
                match = platform.start_match(
                    caller_id=uid(people[0]),
                    match_id=mid,
                    first_turn_player_indices=frozenset({0}),
                    initial_state=None,
                )
                steps = rng.randint(1, 4) if rng.random() < 0.2 else 20
                for _ in range(steps):
                    match = store.get_match(mid)
                    if match.status == "over":
                        break
                    now[0] += dt.timedelta(seconds=rng.randint(1, 120))
                    seat = match.seats[min(match.turn_of_player_indices)]
                    if seat.kind == "computer":
                        platform._run_computer_moves(mid)
                    else:
                        platform.make_move(
                            caller_id=seat.user_id,
                            match_id=mid,
                            move={"add": rng.randint(1, 3)},
                            new_state=None,
                            next_turn_player_indices=None,
                            expected_move_count=len(match.moves),
                            request_id=f"activity:{i}:{len(match.moves)}",
                        )
            store.raw_put(
                manifest,
                {
                    "settings": settings,
                    "done": i + 1,
                    "complete": i + 1 == matches,
                    "month": "2026-09",
                },
            )
    return {
        "complete": True,
        "matches": matches,
        "estimated_write_cap": estimate,
        "kind": "historical service/QuickJS activity; no browser simulation",
    }
