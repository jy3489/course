import pytest

from game_platform.tests.helpers import Api


@pytest.fixture
def api() -> Api:
    return Api()
