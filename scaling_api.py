"""Bounded endpoints. Registered separately so existing clients can migrate."""

import re
from dataclasses import replace
from fastapi import HTTPException, Query
from game_platform.schemas import GameSummaryOut, GameDraftOut, UserOut, MoveOut


def install(app, *, debug_tools):
    from game_platform.api import (
        Platform,
        CallerId,
        OptionalCallerId,
        MasterPassword,
        _match_out,
    )

    def store_of(platform):
        store = platform._store
        if not getattr(store, "scalable", False):
            raise HTTPException(
                409, "paginated storage is not enabled on this deployment"
            )
        return store

    def page(store, kind, **kwargs):
        try:
            return store.page(kind, **kwargs)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/storage/capabilities")
    def capabilities(platform: Platform):
        enabled = getattr(platform._store, "scalable", False)
        return {
            "pagination": enabled,
            "page_size": 50,
            "max_page_size": 100,
            "search": "word-prefix" if enabled else "substring",
        }

    @app.get("/v2/games")
    def games(
        platform: Platform,
        q: str = "",
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = None,
    ):
        store = store_of(platform)
        filters = [("deleted", "==", False), ("published", "==", True)]
        if q:
            token = q.strip().casefold()
            if not re.fullmatch(r"\w{2,24}", token):
                raise HTTPException(
                    400, "search requires one word prefix of 2 to 24 characters"
                )
            filters.append(("search_tokens", "array_contains", token))
        result = page(
            store, "games", filters=filters, limit=limit, cursor=cursor, scope="public"
        )
        return {
            "items": [GameSummaryOut.of(x) for x in result.items],
            "next_cursor": result.next_cursor,
        }

    @app.get("/v2/builder/games")
    def owned_games(
        platform: Platform,
        caller_id: CallerId,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = None,
    ):
        result = page(
            store_of(platform),
            "games",
            filters=[("owner_user_id", "==", caller_id), ("deleted", "==", False)],
            limit=limit,
            cursor=cursor,
            scope=caller_id,
        )
        return {
            "items": [GameSummaryOut.of(x) for x in result.items],
            "next_cursor": result.next_cursor,
        }

    @app.get("/v2/game-drafts")
    def drafts(
        platform: Platform,
        caller_id: CallerId,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = None,
    ):
        result = page(
            store_of(platform),
            "game_drafts",
            filters=[("owner_user_id", "==", caller_id)],
            limit=limit,
            cursor=cursor,
            scope=caller_id,
        )
        return {
            "items": [GameDraftOut.of(x) for x in result.items],
            "next_cursor": result.next_cursor,
        }

    @app.get("/v2/matches/open")
    def open_matches(
        platform: Platform,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = None,
    ):
        store = store_of(platform)
        result = page(
            store,
            "matches",
            filters=[("joinable", "==", True)],
            limit=limit,
            cursor=cursor,
            scope="public-open",
        )
        # Publication can change after a match is created. Check current metadata
        # without fetching game source or updating every historical match.
        items = []
        for match in result.items:
            game = store.raw_get(store.ref("games", match.game_id))
            if game and game.get("published") and not game.get("deleted"):
                items.append(_match_out(platform, match))
        return {"items": items, "next_cursor": result.next_cursor}

    @app.get("/v2/matches")
    def matches(
        platform: Platform,
        caller_id: CallerId,
        status: str | None = None,
        game_id: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = None,
    ):
        filters = [("visible_to", "array_contains", caller_id)]
        if status:
            if status not in ("waiting_for_players", "ongoing", "over"):
                raise HTTPException(400, "invalid status")
            filters.append(("status", "==", status))
        if game_id:
            filters.append(("game_id", "==", game_id))
        result = page(
            store_of(platform),
            "matches",
            filters=filters,
            limit=limit,
            cursor=cursor,
            scope=caller_id,
        )
        return {
            "items": [_match_out(platform, x, caller_id) for x in result.items],
            "next_cursor": result.next_cursor,
        }

    @app.get("/v2/matches/{match_id}/history")
    def history(
        platform: Platform,
        caller_id: OptionalCallerId,
        match_id: str,
        start: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=100),
    ):
        store_of(platform)
        match = platform.get_match(match_id=match_id, caller_id=caller_id)
        stop = min(start + limit, len(match.moves))
        moves = match.moves[start:stop]
        # Apply per-player views to history as well as current state.
        items = [
            MoveOut.of(
                replace(
                    move,
                    new_state=platform.view_state(
                        replace(match, state=move.new_state), caller_id
                    ),
                )
            )
            for move in moves
        ]
        initial = platform.view_state(
            replace(match, state=match.initial_state), caller_id
        )
        return {
            "items": items,
            "next_start": stop if stop < len(match.moves) else None,
            "total": len(match.moves),
            "initial_state": initial,
            "initial_turn_player_indices": match.initial_turn_player_indices,
        }

    if debug_tools:

        @app.get(
            "/v2/admin/{kind}", dependencies=[MasterPassword], include_in_schema=False
        )
        def browse(
            platform: Platform,
            kind: str,
            limit: int = Query(50, ge=1, le=100),
            cursor: str | None = None,
        ):
            if kind not in (
                "users",
                "games",
                "game_versions",
                "matches",
                "game_drafts",
                "game_version_errors",
                "agent_conversations",
            ):
                raise HTTPException(404, "unknown collection")
            result = page(
                store_of(platform), kind, limit=limit, cursor=cursor, scope="admin"
            )
            if kind == "users":
                items = [UserOut.of(x) for x in result.items]
            elif kind == "matches":
                items = [_match_out(platform, x) for x in result.items]
            elif kind == "game_drafts":
                items = [GameDraftOut.of(x) for x in result.items]
            elif kind in ("games", "game_versions"):
                items = [GameSummaryOut.of(x) for x in result.items]
            else:
                items = result.items
            return {"items": items, "next_cursor": result.next_cursor}
