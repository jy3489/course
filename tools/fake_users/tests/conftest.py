import sys
from pathlib import Path

# Run with `python -m pytest tests` from tools/fake_users; this makes `fake_users` importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
