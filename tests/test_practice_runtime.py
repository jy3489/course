"""Keep browser practice on the same deterministic API as server games."""
from pathlib import Path

from game_platform.game_logic import _PRELUDE


def test_browser_practice_uses_the_server_runtime_prelude():
    source = (Path(__file__).parents[1] / "static" / "portal-practice.js").read_text()
    browser_prelude = source.split("const PRACTICE_PRELUDE = String.raw`", 1)[1].split("`;", 1)[0]
    assert browser_prelude == _PRELUDE
