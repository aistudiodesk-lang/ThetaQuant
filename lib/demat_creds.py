"""Per-demat credentials — contract-note PDF passwords (and broker/client) set up ONCE
per demat during setup, so the reconciliation ingestion never asks Rohan to type a
password per upload.

Stored LOCALLY on the host at ~/.config/thetadesk_demat_creds.json with 0600 perms
(owner read/write only) — OUTSIDE the repo, never in git, training, or memory. Same
trust model as the existing Kite credentials. Passwords are never sent to the browser
(API masks them); only set/replace is allowed from the UI.
"""
from __future__ import annotations
from pathlib import Path
import json
import os

STORE = Path.home() / ".config" / "thetadesk_demat_creds.json"


def _load() -> dict:
    try:
        return json.loads(STORE.read_text()) if STORE.exists() else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(d, indent=2))
    try:
        os.chmod(STORE, 0o600)            # owner read/write only
    except Exception:
        pass


def set_demat(code: str, password: str | None = None, broker: str | None = None,
              client: str | None = None) -> bool:
    code = (code or "").strip().upper()
    if not code:
        return False
    d = _load()
    rec = d.get(code, {})
    if password is not None and password != "":
        rec["password"] = password         # plaintext on a 0600 local file (Kite-creds model)
    if broker is not None:
        rec["broker"] = broker.strip()
    if client is not None:
        rec["client"] = client.strip()
    d[code] = rec
    _save(d)
    return True


def get_password(code: str) -> str | None:
    return _load().get((code or "").strip().upper(), {}).get("password")


def delete_demat(code: str) -> bool:
    d = _load()
    if (code or "").strip().upper() in d:
        d.pop((code or "").strip().upper())
        _save(d)
        return True
    return False


def list_demats(mask: bool = True) -> list[dict]:
    """List configured demats. Passwords NEVER returned in clear — masked for the UI."""
    out = []
    for code, rec in _load().items():
        out.append({
            "demat": code, "broker": rec.get("broker", ""), "client": rec.get("client", ""),
            "has_password": bool(rec.get("password")),
            "password_mask": ("••••••••" if rec.get("password") else ""),
        })
    return sorted(out, key=lambda x: x["demat"])
