from game_platform.tests.helpers import Api, error


def test_solo_game_can_start_continue_and_finish_with_one_human(api: Api):
    alice = api.new_user('solo player')
    game = api.new_game(owner=alice, allowed_player_counts=[1])
    match = api.new_match(owner=alice, game_id=game, start=True)
    assert api.match_summary(match)['players'] == ['solo player']
    assert api.match_summary(match)['turn'] == [0]
    assert api.move(match, as_user=alice, next_turn=0, state={'score': 1}).status_code == 201
    assert api.match_summary(match)['turn'] == [0]
    assert api.move(match, as_user=alice, next_turn=None, state={'score': 2}).status_code == 201
    assert api.match_summary(match)['status'] == 'over'


def test_solo_only_game_cannot_add_a_second_seat(api: Api):
    alice, bob = api.new_user('alice'), api.new_user('bob')
    game = api.new_game(owner=alice, allowed_player_counts=[1])
    match = api.new_match(owner=alice, game_id=game)
    assert error(api.request('POST', f'/matches/{match}/join', as_user=bob)) == (409, 'the match is full')
    assert api.request('POST', '/matches', as_user=alice, json={'game_id': game, 'num_computer_opponents': 1}).status_code == 400


def test_same_game_can_offer_solo_and_multiplayer(api: Api):
    alice, bob = api.new_user('alice'), api.new_user('bob')
    game = api.new_game(owner=alice, allowed_player_counts=[1, 2])
    solo = api.new_match(owner=alice, game_id=game, start=True)
    humans = api.new_match(owner=alice, game_id=game, joiners=[bob], start=True)
    computer = api.new_match(owner=alice, game_id=game, num_computer_opponents=1, start=True)
    assert api.match_summary(solo)['players'] == ['alice']
    assert api.match_summary(humans)['players'] == ['alice', 'bob']
    assert api.move(computer, as_user=alice, next_turn=1).status_code == 201
    assert api.move(computer, as_user=alice, next_turn=0).status_code == 201
