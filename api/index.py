import sys, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dashboard.server import app
except Exception as _e:
    import fastapi
    app = fastapi.FastAPI()

    @app.get("/{path:path}")
    def _error(path: str = ""):
        return {"startup_error": traceback.format_exc(), "python": sys.version, "cwd": str(Path.cwd()), "file": __file__}
