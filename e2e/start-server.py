"""Starts a throwaway game server for the Playwright UI tests.

The code imports itself as `game_platform`, so this script links the repo under
that name in a temp folder and runs the server from there, with an empty data file.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
port = os.environ.get("E2E_PORT", "8123")

with tempfile.TemporaryDirectory() as tmp:
    (Path(tmp) / "game_platform").symlink_to(repo, target_is_directory=True)
    config = Path(tmp) / "config.json"
    config.write_text(
        '{"server": {"host": "127.0.0.1", "port": %s},'
        ' "data_file": {"path": "%s"}}' % (port, (Path(tmp) / "data.json").as_posix())
    )
    sys.exit(
        subprocess.call(
            [sys.executable, "-m", "game_platform.main", "--config", str(config)],
            cwd=tmp,
        )
    )
