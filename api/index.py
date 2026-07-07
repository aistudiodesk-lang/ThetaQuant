import sys
from pathlib import Path

# Ensure repo root is on the path so `lib/` and `dashboard/` are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.server import app
