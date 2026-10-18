"""Server-verified Supabase identity and tenant-scoped console authorization.

The browser receives only an opaque HttpOnly cookie. Supabase passwords/tokens
are never written to the document store, traces, browser storage or logs.
"""
from __future__ import annotations
import hashlib
import hmac
import os
import secrets
import time
import threading
from urllib.parse import urlsplit
import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from zar_core import config

router = APIRouter(prefix="/api/console")
ROLES = {"administrator", "operations", "analyst", "auditor"}
COOKIE = "zar_console"
_rates = {}
_rates_at = 0
_rates_lock = threading.Lock()

@router.get("/rates")
def rates():
    """Reference conversion only. Merchant quotes always remain in INR."""
    global _rates, _rates_at
    with _rates_lock:
        if time.time() - _rates_at < 3600 and _rates:
            return _rates
        try:
            r = requests.get("https://api.frankfurter.dev/v1/latest", params={"base": "INR", "symbols": "USD,EUR"}, timeout=(3, 5), allow_redirects=False)
            r.raise_for_status()
            data = r.json()
            values = {k: float(data["rates"][k]) for k in ("USD", "EUR")}
            if data.get("base") != "INR" or any(not 0 < v < 10000 for v in values.values()):
                raise ValueError("Invalid rates")
            _rates = {"rates": {"INR": 1, **{k: 1/v for k, v in values.items()}}, "date": data["date"], "source": "https://frankfurter.dev/v1/"}
            _rates_at = time.time()
            return _rates
        except (requests.RequestException, ValueError, KeyError):
            raise HTTPException(503, "Reference rates are unavailable. Amounts remain in INR") from None

def backend():
    from . import main
    return main

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def writes(request: Request):
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if request.headers.get("x-zar-console") != "1" or (origin and urlsplit(origin).netloc != request.headers.get("host")):
        raise HTTPException(403, "Use the same-origin ZAR console")

def local_enabled(request: Request):
    m = backend()
    return m.DEMO_MODE and not os.getenv("K_SERVICE") and request.client and request.client.host in {"127.0.0.1", "::1", "testclient"} and request.url.hostname in {"localhost", "127.0.0.1", "::1", "testserver"}

def auth_enabled():
    return bool(config.SUPABASE_URL and os.getenv("ZAR_SUPABASE_PUBLISHABLE_KEY"))

def viewer(request: Request):
    token = request.cookies.get(COOKIE, "")
    store = backend().APP.runtime.store
    session = store.get("console_sessions", digest(token)) if token else None
    if not session or session["expires"] <= time.time():
        raise HTTPException(401, "Please sign in")
    if session["user_id"] == "local-development":
        if not local_enabled(request):
            raise HTTPException(401, "Local development access is disabled")
        return {"id": "local-development", "email": "local@localhost", "name": "Local developer", "role": "administrator", "stores": ["store6"], "active": True}
    member = store.get("console_members", session["user_id"])
    if not member or not member.get("active") or member.get("role") not in ROLES:
        raise HTTPException(403, "Workspace access has not been assigned")
    return member

def require_role(user, *roles):
    if user["role"] not in roles:
        raise HTTPException(403, "Your role cannot perform this action")

def scoped(user, tenant):
    if tenant not in user.get("stores", []):
        raise HTTPException(404, "Store or order not found")

def view(user):
    return {"username": user["email"], "user": {k: user[k] for k in ("id", "name", "email", "role", "stores")} | {"packs": ["retail"]}}

def cookie(response, request, user_id, lifetime=3600):
    token = secrets.token_urlsafe(32)
    backend().APP.runtime.store.set("console_sessions", digest(token), {"user_id": user_id, "expires": time.time() + lifetime})
    response.set_cookie(COOKIE, token, httponly=True, secure=bool(os.getenv("K_SERVICE")) or request.url.scheme == "https", samesite="strict", max_age=lifetime, path="/")
    response.headers["Cache-Control"] = "no-store"

class Login(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)

@router.get("/config")
def settings(request: Request):
    return {"auth": auth_enabled(), "local": bool(local_enabled(request)), "store": config.STORE_MODE,
            "llm": config.LLM_MODE, "models": {"fast": config.MODEL_FAST, "reasoning": config.MODEL_REASONING},
            "release": config.RELEASE_ID}

@router.get("/me")
def me(response: Response, user=Depends(viewer)):
    response.headers["Cache-Control"] = "no-store"
    return view(user)

@router.post("/login", dependencies=[Depends(writes)])
def login(body: Login, request: Request, response: Response):
    if not auth_enabled():
        raise HTTPException(503, "Configure Supabase Auth first; see the deployment guide")
    url = config.SUPABASE_URL.rstrip("/")
    if urlsplit(url).scheme != "https" or not urlsplit(url).hostname:
        raise HTTPException(503, "Supabase Auth requires an HTTPS project URL")
    store = backend().APP.runtime.store
    email = body.email.strip().lower()
    key = digest(email)
    with store.lock("login-" + key):
        attempts = store.get("console_attempts", key) or {"start": time.time(), "count": 0}
        if time.time() - attempts["start"] > 900:
            attempts = {"start": time.time(), "count": 0}
        if attempts["count"] >= 10:
            raise HTTPException(429, "Too many sign-in attempts. Try again in 15 minutes")
        attempts["count"] += 1
        store.set("console_attempts", key, attempts)
    try:
        headers = {"apikey": os.environ["ZAR_SUPABASE_PUBLISHABLE_KEY"]}
        result = requests.post(url + "/auth/v1/token?grant_type=password", json={"email": email, "password": body.password}, headers=headers, timeout=(5, 20), allow_redirects=False)
        if result.status_code != 200:
            raise HTTPException(401, "Email or password was not accepted")
        data = result.json()
        check = requests.get(url + "/auth/v1/user", headers={**headers, "Authorization": "Bearer " + data["access_token"]}, timeout=(5, 20), allow_redirects=False)
        if check.status_code != 200:
            raise HTTPException(401, "Sign-in could not be verified")
        identity = check.json()
        if not identity.get("email_confirmed_at") or identity.get("email", "").lower() != email:
            raise HTTPException(403, "Confirm your email before signing in")
        user_id = str(identity["id"])
    except (requests.RequestException, ValueError, KeyError):
        raise HTTPException(503, "Supabase sign-in is temporarily unavailable") from None
    with store.lock("member-" + user_id):
        member = store.get("console_members", user_id)
        owner = os.getenv("ZAR_BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
        if not member and owner and hmac.compare_digest(owner, email):
            member = {"id": user_id, "email": email, "name": email.split("@")[0], "role": "administrator", "stores": ["store6"], "active": True}
            store.set("console_members", user_id, member)
    if not member or not member.get("active"):
        raise HTTPException(403, "Ask your workspace administrator to assign your Supabase user ID and role")
    store.set("console_attempts", key, {"start": time.time(), "count": 0})
    cookie(response, request, user_id, min(3600, max(60, int(data.get("expires_in", 3600)))))
    return view(member)

class LocalLogin(BaseModel):
    token: str = Field("", max_length=1024)

@router.post("/local-login", dependencies=[Depends(writes)])
def local_login(body: LocalLogin, request: Request, response: Response):
    if not local_enabled(request):
        raise HTTPException(404, "Not available")
    backend().admin(body.token or None)
    cookie(response, request, "local-development")
    return {"ok": True}

@router.post("/logout", dependencies=[Depends(writes)])
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        backend().APP.runtime.store.set("console_sessions", digest(token), {"user_id": "", "expires": 0})
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}
