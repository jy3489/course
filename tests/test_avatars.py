from pathlib import Path
import struct
from dataclasses import replace
from game_platform.avatars import USER_AVATARS, COMPUTER_AVATARS, computer_avatar
from game_platform.service import GamePlatform
from game_platform.store import InMemoryStore


def test_catalog_has_1010_distinct_existing_images():
    root = Path(__file__).resolve().parents[1]
    assert len(set(USER_AVATARS)) == 1000
    assert len(set(COMPUTER_AVATARS)) == 10
    for url in (*USER_AVATARS, *COMPUTER_AVATARS):
        data = (root / url.lstrip('/')).read_bytes()
        assert data.startswith(b'\x89PNG')
        assert struct.unpack('>II', data[16:24]) == (96, 96)


def test_assignment_persists_and_provider_photo_is_preserved():
    store = InMemoryStore()
    platform = GamePlatform(store=store)
    user = platform.create_user(display_name='Guest')
    assert user.picture_url in USER_AVATARS
    provider = platform.create_user(display_name='Provider', picture_url='https://example.com/photo.png')
    GamePlatform(store=store)
    assert store.get_user(user.id).picture_url == user.picture_url
    assert store.get_user(provider.id).picture_url == provider.picture_url
    store.put_user(replace(user, picture_url=''))
    GamePlatform(store=store)
    saved = store.get_user(user.id).picture_url
    assert saved in USER_AVATARS
    GamePlatform(store=store)
    assert store.get_user(user.id).picture_url == saved
    assert computer_avatar('computer-2') == computer_avatar('computer-2')
