"""
Access control for Theta Quant — per-user login + scoped permissions.

Model (chosen defaults):
  • Each user has a ROLE   → admin | editor | viewer  (controls EDIT vs view-only)
  • Each user has SCOPES   → 4 dimensions, each a list of allowed values or ["*"] = all:
        clients     (e.g. ["RHS"])      — which client books they see
        strategies  (e.g. ["EXPIRY"])   — which strategy desks they can open
        brokers     (e.g. ["Axis"])     — which brokers' rows in reporting
        demats      (e.g. ["*"])         — which demat accounts' rows

Store: ~/.config/thetadesk_users.json (mode 600, never in git). Passwords are
salted-SHA256 hashed — plaintext is never stored.

Bootstrap / no-lockout:
  • Loopback (the host / Rohan's own Mac) is ALWAYS admin, full scope.
  • The legacy ~/.config/thetadesk_web.json team account always works as a
    full-scope ADMIN master account (so you can never lock yourself out).
  • Office/LAN devices (TG_TRUSTED_HOSTS) are auto-admin ONLY until you create
    real user accounts; after that they must log in (so limits actually apply).
"""
from __future__ import annotations
from pathlib import Path
import hashlib
import json
import secrets

STORE = Path.home() / ".config" / "thetadesk_users.json"

ROLES = ("admin", "editor", "viewer")
DIMENSIONS = ("clients", "strategies", "brokers", "demats")

# Canonical strategy keys shown in the access UI (kept in sync with STRATEGY_DESKS
# s_codes + the Expiry/Reporting desks which aren't in that table).
STRATEGIES = [
    ("EXPIRY", "Expiry (weekly deep-OTM)"),
    ("S1",     "Covered Call vs Investment"),
    ("S2A",    "Covered Call — Regular OTM"),
    ("S2B",    "Covered Call — ITM theta"),
    ("S3",     "Monthly OTM Index"),
    ("S6",     "Long NIFTY"),
    ("S7",     "Commodity"),
    ("REPORTING", "Reporting & Margin"),
]
STRATEGY_KEYS = [k for k, _ in STRATEGIES]

# Map page paths → the strategy key that gates them (page-level access boundary).
_ROUTE_STRATEGY = {
    "/playbook": "EXPIRY", "/report": "EXPIRY", "/chain": "EXPIRY", "/manipulation": "EXPIRY",
    "/index/monthly": "S3", "/index/long": "S6",
    "/cc/investment": "S1", "/cc/otm": "S2A", "/cc/itm": "S2B",
    "/commodity": "S7",
    "/report-full": "REPORTING", "/margin": "REPORTING",
}

# A synthetic full-access admin used for loopback / legacy master login.
ADMIN = {
    "username": "admin", "role": "admin", "name": "admin",
    "scopes": {d: ["*"] for d in DIMENSIONS},
}


# ── password hashing ─────────────────────────────────────────────────────
def hash_pw(pw: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    h = hashlib.sha256((salt + (pw or "")).encode("utf-8")).hexdigest()
    return f"{salt}${h}"


def verify_pw(pw: str, stored: str) -> bool:
    try:
        salt, h = (stored or "").split("$", 1)
    except ValueError:
        return False
    calc = hashlib.sha256((salt + (pw or "")).encode("utf-8")).hexdigest()
    return secrets.compare_digest(calc, h)


# ── store ────────────────────────────────────────────────────────────────
def _load() -> dict:
    if STORE.exists():
        try:
            d = json.loads(STORE.read_text())
            if isinstance(d, dict) and isinstance(d.get("users"), dict):
                return d
        except Exception:
            pass
    return {"users": {}}


def _save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(d, indent=2))
    try:
        STORE.chmod(0o600)
    except Exception:
        pass


def users_defined() -> bool:
    """True once at least one real user account exists (gates office-WiFi auto-admin)."""
    return bool(_load().get("users"))


def _norm_scopes(scopes: dict | None) -> dict:
    scopes = scopes or {}
    out = {}
    for d in DIMENSIONS:
        v = scopes.get(d)
        if not v or "*" in v:
            out[d] = ["*"]
        else:
            out[d] = [str(x) for x in v]
    return out


def list_users() -> list[dict]:
    """All users WITHOUT password hashes — for the admin UI."""
    out = []
    for uname, rec in sorted(_load().get("users", {}).items()):
        out.append({"username": uname, "role": rec.get("role", "viewer"),
                    "name": rec.get("name", uname),
                    "scopes": _norm_scopes(rec.get("scopes"))})
    return out


def get_user(username: str) -> dict | None:
    rec = _load().get("users", {}).get((username or "").strip())
    if not rec:
        return None
    return {"username": username, "role": rec.get("role", "viewer"),
            "name": rec.get("name", username), "scopes": _norm_scopes(rec.get("scopes"))}


def upsert_user(username: str, role: str, scopes: dict,
                name: str | None = None, password: str | None = None) -> dict:
    username = (username or "").strip()
    if not username:
        raise ValueError("username required")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    d = _load()
    rec = d["users"].get(username, {})
    rec["role"] = role
    rec["name"] = (name or rec.get("name") or username)
    rec["scopes"] = _norm_scopes(scopes)
    if password:
        rec["password"] = hash_pw(password)
    elif "password" not in rec:
        raise ValueError("password required for a new user")
    d["users"][username] = rec
    _save(d)
    return get_user(username)


def delete_user(username: str) -> bool:
    d = _load()
    if username in d.get("users", {}):
        del d["users"][username]
        _save(d)
        return True
    return False


def authenticate(username: str, password: str) -> dict | None:
    """Return the user dict (no password) on success, else None."""
    rec = _load().get("users", {}).get((username or "").strip())
    if not rec:
        return None
    if verify_pw(password, rec.get("password", "")):
        return {"username": username, "role": rec.get("role", "viewer"),
                "name": rec.get("name", username), "scopes": _norm_scopes(rec.get("scopes"))}
    return None


# ── scope checks ─────────────────────────────────────────────────────────
def is_admin(user: dict | None) -> bool:
    return bool(user) and user.get("role") == "admin"


def can_write(user: dict | None) -> bool:
    return bool(user) and user.get("role") in ("admin", "editor")


def scope_list(user: dict | None, dim: str) -> list:
    if not user:
        return ["*"]
    return _norm_scopes(user.get("scopes")).get(dim, ["*"])


def allows(user: dict | None, dim: str, value) -> bool:
    if is_admin(user):
        return True
    lst = scope_list(user, dim)
    return "*" in lst or str(value) in lst
def filter_values(user: dict | None, dim: str, values) -> list:
    lst = scope_list(user, dim)
    if "*" in lst:
        return list(values)
    allow = set(lst)
    return [v for v in values if str(v) in allow]


def route_strategy(path: str) -> str | None:
    """The strategy key gating a page path, or None if not strategy-gated."""
    for prefix, strat in _ROUTE_STRATEGY.items():
        if path == prefix or path.startswith(prefix + "/"):
            return strat
    return None
