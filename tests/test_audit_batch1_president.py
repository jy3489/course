"""Regression cases for President's card shape, ownership and multiplicity checks."""
from copy import deepcopy
from pathlib import Path

import pytest

from game_platform.game_logic import GameLogic, InvalidMoveError

SOURCE = Path(__file__).parent.parent / 'games' / 'audit-batch1' / 'president.server.js'


def started(player_count=3):
    logic = GameLogic(SOURCE.read_text())
    ctx = {
        'players': [
            {'id': f'p{i}', 'kind': 'human', 'display_name': f'P{i}', 'max_loss': 10}
            for i in range(player_count)
        ],
        'pot': 10 * player_count,
        'move_count': 0,
    }
    current = logic.call('newMatch', ctx=ctx, seed=123)
    return logic, ctx, current


@pytest.mark.parametrize('cards', [
    None, [], True, 'cards', {'0': {'r': 3, 's': 'C'}, 'length': 1},
    [None], [{}], [[]], [False], [{'r': 999, 's': 'FAKE'}],
    [{'r': 3.5, 's': 'C'}], [{'r': True, 's': 'C'}],
    [{'r': 2, 's': 'C'}], [{'r': 3, 's': 'X'}],
    [{'r': 16, 's': 'C'}], [{'r': 17, 's': 'SJ'}],
])
def test_malformed_card_selection_is_invalid_move(cards):
    logic, ctx, current = started()
    with pytest.raises(InvalidMoveError):
        logic.call('applyMove', current['state'], current['turn'][0], {'cards': cards}, ctx=ctx)


def test_valid_card_owned_by_someone_else_is_rejected():
    logic, ctx, current = started()
    actor = current['turn'][0]
    seat = int(actor[1:])
    other_card = current['state']['hands'][(seat + 1) % 3][0]
    with pytest.raises(InvalidMoveError, match='card not in hand'):
        logic.call('applyMove', current['state'], actor, {'cards': [other_card]}, ctx=ctx)


def test_repeating_one_owned_card_does_not_create_a_pair():
    logic, ctx, current = started()
    actor = current['turn'][0]
    card = current['state']['hands'][int(actor[1:])][0]
    with pytest.raises(InvalidMoveError, match='card not in hand'):
        logic.call('applyMove', current['state'], actor, {'cards': [card, card]}, ctx=ctx)


def test_invalid_card_cannot_end_match_at_move_cap():
    logic, ctx, current = started()
    state = deepcopy(current['state'])
    state['totalMoves'] = 120
    with pytest.raises(InvalidMoveError):
        logic.call('applyMove', state, current['turn'][0], {'cards': [None]}, ctx=ctx)


def test_valid_owned_single_is_removed_and_played():
    logic, ctx, current = started()
    actor = current['turn'][0]
    seat = int(actor[1:])
    card = current['state']['hands'][seat][0]
    result = logic.call('applyMove', current['state'], actor, {'cards': [card]}, ctx=ctx)
    assert result['state']['pile'] == [card]
    assert result['state']['hands'][seat] == current['state']['hands'][seat][1:]


def move_cards_to_actor(state, actor_seat, wanted):
    """Move existing cards between hands without inventing cards for legal variants."""
    for card in wanted:
        for seat, hand in enumerate(state['hands']):
            if card in hand:
                if seat != actor_seat:
                    hand.remove(card)
                    state['hands'][actor_seat].append(card)
                break


@pytest.mark.parametrize('cards, expected_bomb', [
    ([{'r': 8, 's': 'C'}, {'r': 8, 's': 'D'}], False),
    ([{'r': 16, 's': 'SJ'}, {'r': 17, 's': 'BJ'}], True),
    ([{'r': 15, 's': 'S'}], True),
])
def test_owned_pairs_jokers_and_deuce_variant_remain_legal(cards, expected_bomb):
    logic, ctx, current = started()
    actor = current['turn'][0]
    seat = int(actor[1:])
    state = deepcopy(current['state'])
    move_cards_to_actor(state, seat, cards)
    before = len(state['hands'][seat])
    result = logic.call('applyMove', state, actor, {'cards': cards}, ctx=ctx)
    assert len(result['state']['hands'][seat]) == before - len(cards)
    if expected_bomb:
        assert result['state']['pile'] == []
        assert result['turn'] == [actor]
    else:
        assert result['state']['pile'] == cards


@pytest.mark.parametrize('player_count', [3, 8])
def test_seeded_full_match_move_generators_remain_accepted(player_count):
    logic, ctx, current = started(player_count)
    for step in range(200):
        if current['turn'] is None:
            break
        actor = current['turn'][0]
        move = logic.call('computerMove' if step % 2 else 'randomMove',
                          current['state'], actor, ctx=ctx, seed=step + 123)
        current = logic.call('applyMove', current['state'], actor, move, ctx=ctx, seed=step + 123)
        ctx['move_count'] += 1
    assert current['turn'] is None
    assert sum(current['payouts'].values()) == ctx['pot']


def test_last_card_equal_rank_skip_does_not_return_turn_to_finished_player():
    logic, ctx, current = started(7)
    state = deepcopy(current['state'])
    # Reachable regression seed1663594182: seat2 matches the last ten;
    # all other unfinished seats have passed, and the old leader is out.
    state.update(
        hands=[
            [{'r': 4, 's': 'H'}], [{'r': 5, 's': 'H'}],
            [{'r': 10, 's': 'C'}], [], [], [], [{'r': 4, 's': 'C'}],
        ],
        pile=[{'r': 10, 's': 'H'}], pileRank=10, pileCount=1,
        trickCount=1, trickRankCount={'10': 1}, leader=5,
        passed=[True, True, False, False, False, False, True],
        finished=[3, 4, 5], totalMoves=67,
    )
    result = logic.call('applyMove', state, 'p2', {'cards': [{'r': 10, 's': 'C'}]}, ctx=ctx)
    assert 2 in result['state']['finished']
    assert result['turn'] == ['p6']
    assert result['state']['pile'] == []
    assert result['state']['passed'] == [False] * 7
    # The nominated player can actually make their generated move.
    move = logic.call('randomMove', result['state'], 'p6', ctx=ctx)
    logic.call('applyMove', result['state'], 'p6', move, ctx=ctx)


def test_platform_random_walk_regression_for_finished_skip_player():
    from game_platform.game_testing import test_server_code as run_gate
    from game_platform.models import GameRules

    report = run_gate(
        SOURCE.read_text(),
        rules=GameRules(allowed_player_counts=(3, 4, 5, 6, 7, 8),
                        allows_leave_mid_match=False, allows_join_mid_match=False),
        regression_seeds=(1663594182,), budget_seconds=2, max_walks=1,
    )
    assert report.passed, report.failures
