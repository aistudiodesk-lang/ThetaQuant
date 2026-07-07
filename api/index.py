"""
Diagnostic shim for Vercel — has top-level app = FastAPI() so Vercel's static
analysis accepts it. The /_diag endpoint runs the risky imports inside a handler
and reports exactly what fails, without crashing the entire function at startup.
"""
import sys, os
from pathlib import Path
from fastapi import FastAPI

app = FastAPI()

@app.get("/_diag")
def diag():
    ROOT = str(Path(__file__).resolve().parent.parent)
    sys.path.insert(0, ROOT)
    results = {"python": sys.version, "cwd": os.getcwd(), "file": __file__, "errors": []}

    # 1. Check static / templates dirs
    static_dir = Path(__file__).resolve().parent.parent / "dashboard" / "static"
    tmpl_dir = Path(__file__).resolve().parent.parent / "dashboard" / "templates"
    results["static_exists"] = static_dir.exists()
    results["templates_exists"] = tmpl_dir.exists()

    # 2. Try lib.playbook
    try:
        import lib.playbook
        results["lib_playbook"] = "ok"
    except Exception as e:
        results["lib_playbook"] = f"FAIL: {type(e).__name__}: {e}"
        results["errors"].append(results["lib_playbook"])

    # 3. Try heavy deps
    for pkg in ["pandas", "numpy", "duckdb", "kiteconnect", "supabase"]:
        try:
            __import__(pkg)
            results[pkg] = "ok"
        except Exception as e:
            results[pkg] = f"FAIL: {type(e).__name__}: {e}"
            results["errors"].append(f"{pkg}: {e}")

    # 4. Try the actual server import (heaviest test)
    try:
        import dashboard.server  # noqa: F401
        results["dashboard_server"] = "ok"
    except Exception as e:
        import traceback
        results["dashboard_server"] = f"FAIL: {type(e).__name__}: {e}"
        results["dashboard_server_tb"] = traceback.format_exc()
        results["errors"].append(results["dashboard_server"])

    return results


@app.get("/{path:path}")
def catch_all(path: str = ""):
    return {"mode": "diagnostic", "hit": "/_diag", "path": path}
