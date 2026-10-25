"""
Owner-only dashboard (web) served on the same port as /health. Exists ONLY in STRING_SESSION (userbot) mode.

  GET  /admin                   login form, or the Telegram-style app once logged in
  GET  /admin/static/<file>     app.js / app.css (login required)
  POST /admin/login, /logout    password check (rate limited), signed HttpOnly cookie
  GET  /admin/api/users|messages   AI chat log (masked `chat_log` collection, auto-deleted after LOG_DAYS)
  GET  /admin/api/tg/<op>       read operations of the Telegram account (chats, history, profile, privacy, sessions ...)
  POST /admin/api/tg/<op>       write operations (send, edit, profile, settings ...), JSON body, CSRF header required
  POST /admin/api/upload/<kind> file uploads (photo | file)
  GET  /admin/api/media|avatar  media bytes (login required)
  GET  /admin/api/events        live updates (server-sent events)

Off unless ADMIN_PASSWORD is set (and STRING_SESSION is used). Security: strong password + rate limit, HttpOnly SameSite=Strict
cookie, CSRF token on every write, Origin check, strict CSP (no inline script), chat text rendered with textContent only.
"""
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import deque
from datetime import datetime, timezone

from config import (
    ADMIN_LOGIN_MAX_ATTEMPTS,
    ADMIN_LOGIN_WINDOW,
    ADMIN_PASSWORD,
    ADMIN_SESSION_HOURS,
    API_HASH,
    DASHBOARD_MAX_PAGE,
    DASHBOARD_PAGE_SIZE,
)
from memory import FLAGS
from tgadmin import EVENTS, AdminError, TgAdmin
from util import BoundedDict

COOKIE = "monitor_session"
BASE = "/admin"
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
SAFE_INLINE = {"image/jpeg", "image/png", "image/gif", "image/webp", "video/mp4", "video/webm", "audio/mpeg", "audio/ogg",
               "audio/mp4", "audio/x-m4a", "audio/aac", "audio/wav"}
STATIC_FILES = {"app.js": "application/javascript; charset=utf-8", "app.css": "text/css; charset=utf-8"}


# ------------------------------------------------------------------ page templates
_STYLE = """
:root{color-scheme:dark light;--bg:#0f1115;--card:#171a21;--line:#262b36;--text:#e8eaf0;--mute:#8b93a7;--me:#2b6cff;--bot:#262b36;--accent:#6ea8ff}
@media (prefers-color-scheme:light){:root{--bg:#f4f5f8;--card:#fff;--line:#e2e5ec;--text:#161922;--mute:#667085;--bot:#e8ebf2;--accent:#2b6cff}}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);font:15px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:8px;padding:10px 12px;background:var(--card);border-bottom:1px solid var(--line)}
h1{font-size:16px;margin:0;flex:1}button,input{font:inherit;color:inherit}
button{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:7px 12px;cursor:pointer}
button.active{background:var(--accent);border-color:var(--accent);color:#fff}
.bar{padding:10px 12px;display:flex;flex-direction:column;gap:8px}.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{border-radius:999px;padding:5px 11px;font-size:13px}
input[type=search],input[type=password]{width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:var(--card)}
main{padding:0 12px 40px;max-width:760px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:11px 13px;margin:8px 0;cursor:pointer}
.row{display:flex;justify-content:space-between;gap:8px;align-items:baseline}.name{font-weight:600}.mute{color:var(--mute);font-size:13px}
.badges{display:flex;gap:5px;flex-wrap:wrap;margin-top:6px}
.badge{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--line)}
.f-love{background:#ff4d8d22;border-color:#ff4d8d}.f-real{background:#ffb02022;border-color:#ffb020}.f-meet{background:#a855f722;border-color:#a855f7}
.f-minor{background:#ff3b3b33;border-color:#ff3b3b}.f-distress{background:#ff3b3b33;border-color:#ff3b3b}.f-money{background:#22c55e22;border-color:#22c55e}
.msg{max-width:84%;padding:8px 11px;border-radius:14px;margin:5px 0;white-space:pre-wrap;word-break:break-word}
.in{background:var(--bot);border-bottom-left-radius:4px}.out{background:var(--me);color:#fff;margin-left:auto;border-bottom-right-radius:4px}
.meta{font-size:11px;color:var(--mute);margin:8px 2px 0}.out+.meta{text-align:right}.who{font-weight:600;margin-top:12px}
.empty,.err{padding:24px;text-align:center;color:var(--mute)}.err{color:#ff6b6b}
.login{max-width:340px;margin:18vh auto;padding:0 16px}.login form{display:flex;flex-direction:column;gap:10px}
.login .err{padding:0;text-align:left}
"""

_LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Sign in</title><style nonce="__NONCE__">__STYLE__</style></head><body>
<div class="login"><h1>Account dashboard</h1><p class="mute">Owner only.</p>
<form method="post" action="/admin/login"><input type="password" name="password" placeholder="Password" autocomplete="current-password" autofocus required>
<button class="active" type="submit">Sign in</button>__ERROR__</form></div></body></html>"""

def _nonce() -> str:
    return secrets.token_urlsafe(16)


# ------------------------------------------------------------------ dashboard
class Dashboard:
    def __init__(self, get_memory, password: str = ADMIN_PASSWORD, secret_seed: str = API_HASH,
                 max_attempts: int = ADMIN_LOGIN_MAX_ATTEMPTS, window: int = ADMIN_LOGIN_WINDOW,
                 session_hours: int = ADMIN_SESSION_HOURS, page_size: int = DASHBOARD_PAGE_SIZE,
                 max_page: int = DASHBOARD_MAX_PAGE, control=None, admin: "TgAdmin | None" = None):
        self.get_memory = get_memory
        self.control = control
        self.admin = admin
        self._static: dict = {}
        self.password = password
        # changing the password (or API_HASH) invalidates every existing session
        self._key = hashlib.sha256(f"{password}|{secret_seed}".encode()).digest()
        self.max_attempts, self.window = max_attempts, window
        self.session_seconds = session_hours * 3600
        self.page_size, self.max_page = page_size, max_page
        self._attempts = BoundedDict(1000)      # ip -> deque of failed-login times

    # ---- session cookie
    def _sign(self, expiry: int) -> str:
        return hmac.new(self._key, str(expiry).encode(), hashlib.sha256).hexdigest()

    def make_cookie(self, now: "float | None" = None) -> str:
        expiry = int((now if now is not None else time.time()) + self.session_seconds)
        return f"{expiry}.{self._sign(expiry)}"

    def valid_cookie(self, value: "str | None", now: "float | None" = None) -> bool:
        try:
            expiry_s, sig = (value or "").split(".", 1)
            expiry = int(expiry_s)
        except ValueError:
            return False
        if expiry < (now if now is not None else time.time()):
            return False
        return hmac.compare_digest(sig, self._sign(expiry))

    def authed(self, request) -> bool:
        return self.valid_cookie(request.cookies.get(COOKIE))

    # ---- login throttling
    @staticmethod
    def client_ip(request) -> str:
        forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return forwarded or getattr(request, "remote", None) or "?"

    def locked(self, ip: str, now: "float | None" = None) -> bool:
        now = time.time() if now is None else now
        q = self._attempts.get(ip)
        if not q:
            return False
        while q and q[0] < now - self.window:
            q.popleft()
        return len(q) >= self.max_attempts

    def record_failure(self, ip: str, now: "float | None" = None) -> None:
        q = self._attempts.get(ip)
        if q is None:
            q = self._attempts[ip] = deque()
        q.append(time.time() if now is None else now)

    # ---- responses
    @staticmethod
    def _headers(nonce: "str | None" = None, app: bool = False) -> dict:
        csp = ("default-src 'none'; connect-src 'self'; form-action 'self'; base-uri 'none'; "
               "frame-ancestors 'none'; img-src 'self' data: blob:")
        if app:      # the dashboard app: scripts/styles only from our own files, no inline code at all
            csp += "; media-src 'self' blob:; script-src 'self'; style-src 'self'"
        elif nonce:
            csp += f"; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'"
        return {
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex, nofollow",
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": csp,
        }

    def _html(self, template: str, status: int = 200, error: str = ""):
        from aiohttp import web
        nonce = _nonce()
        body = (template.replace("__STYLE__", _STYLE).replace("__NONCE__", nonce)
                .replace("__FLAGS__", json.dumps(list(FLAGS)))
                .replace("__ERROR__", f'<div class="err">{error}</div>' if error else ""))
        return web.Response(text=body, status=status, content_type="text/html", headers=self._headers(nonce))

    def _json(self, data: dict, status: int = 200):
        from aiohttp import web
        return web.json_response(data, status=status, headers=self._headers())

    # ---- handlers
    # ---- CSRF + same-origin guard for every write
    def csrf_token(self, request) -> str:
        return hmac.new(self._key, ("csrf|" + (request.cookies.get(COOKIE) or "")).encode(), hashlib.sha256).hexdigest()

    def _same_origin(self, request) -> bool:
        origin = request.headers.get("Origin")
        if not origin:
            return True                      # same-origin fetches may omit it; SameSite=Strict + CSRF header still apply
        host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or ""
        return origin.split("://", 1)[-1].rstrip("/") == host

    def _guard(self, request, write: bool = False):
        """None if allowed, else the error response."""
        if not self.authed(request):
            return self._json({"error": "unauthorized"}, 401)
        if write:
            sent = request.headers.get("X-CSRF-Token", "")
            if not hmac.compare_digest(sent.encode(), self.csrf_token(request).encode()) or not self._same_origin(request):
                return self._json({"error": "bad csrf token - page refresh karo"}, 403)
        return None

    def _read_static(self, name: str) -> "str | None":
        if name not in self._static:
            try:
                with open(os.path.join(WEB_DIR, name), encoding="utf-8") as fh:
                    self._static[name] = fh.read()
            except OSError:
                self._static[name] = None
        return self._static[name]

    async def page(self, request):
        from aiohttp import web
        if not self.authed(request):
            return self._html(_LOGIN_HTML)
        html = self._read_static("index.html")
        if html is None:
            return self._json({"error": "web/index.html missing"}, 500)
        html = html.replace("__CSRF__", self.csrf_token(request))
        return web.Response(text=html, content_type="text/html", headers=self._headers(app=True))

    async def static(self, request):
        from aiohttp import web
        if not self.authed(request):
            return self._json({"error": "unauthorized"}, 401)
        name = request.match_info.get("name", "")
        body = self._read_static(name) if name in STATIC_FILES else None
        if body is None:
            return self._json({"error": "not found"}, 404)
        return web.Response(text=body, headers={**self._headers(app=True), "Content-Type": STATIC_FILES[name], "Cache-Control": "no-cache"})

    async def login(self, request):
        from aiohttp import web
        ip = self.client_ip(request)
        if self.locked(ip):
            return self._html(_LOGIN_HTML, 429, "Too many attempts. Try again later.")
        try:
            form = await request.post()
            supplied = str(form.get("password", ""))
        except Exception:
            supplied = ""
        if not hmac.compare_digest(supplied.encode(), self.password.encode()):
            self.record_failure(ip)
            return self._html(_LOGIN_HTML, 401, "Wrong password.")
        self._attempts.pop(ip, None)
        resp = web.Response(status=303, headers={**self._headers(), "Location": BASE})
        secure = bool(getattr(request, "secure", False)) or request.headers.get("X-Forwarded-Proto") == "https"
        resp.set_cookie(COOKIE, self.make_cookie(), max_age=self.session_seconds, httponly=True,
                        secure=secure, samesite="Strict", path=BASE)
        return resp

    async def logout(self, request):
        from aiohttp import web
        resp = web.Response(status=303, headers={**self._headers(), "Location": BASE})
        resp.del_cookie(COOKIE, path=BASE)
        return resp

    def _parse_int(self, request, name: str):
        raw = request.query.get(name)
        if raw in (None, ""):
            return None, False
        try:
            return int(raw), False
        except ValueError:
            return None, True

    async def api_users(self, request):
        if not self.authed(request):
            return self._json({"error": "unauthorized"}, 401)
        mem = self.get_memory()
        if mem is None:
            return self._json({"error": "starting"}, 503)
        flag = request.query.get("flag", "")
        if flag and flag not in FLAGS:
            return self._json({"error": "bad flag"}, 400)
        users = await mem.dash_users(request.query.get("q", "").strip(), flag, self.page_size)
        if users is None:
            return self._json({"error": "database unavailable"}, 503)
        return self._json({"users": users})

    async def api_messages(self, request):
        if not self.authed(request):
            return self._json({"error": "unauthorized"}, 401)
        mem = self.get_memory()
        if mem is None:
            return self._json({"error": "starting"}, 503)
        chat_id, bad1 = self._parse_int(request, "chat_id")
        user_id, bad2 = self._parse_int(request, "user_id")
        limit, bad3 = self._parse_int(request, "limit")
        if bad1 or bad2 or bad3:
            return self._json({"error": "bad number"}, 400)
        limit = min(max(limit or self.page_size, 1), self.max_page)
        flag = request.query.get("flag", "")
        if flag and flag not in FLAGS:
            return self._json({"error": "bad flag"}, 400)
        before = None
        raw_before = request.query.get("before", "")
        if raw_before:
            try:
                before = datetime.fromisoformat(raw_before.replace("Z", "+00:00"))
                if before.tzinfo is None:
                    before = before.replace(tzinfo=timezone.utc)
            except ValueError:
                return self._json({"error": "bad date"}, 400)
        result = await mem.dash_messages(chat_id=chat_id, user_id=user_id, q=request.query.get("q", "").strip(),
                                         flag=flag, before=before, limit=limit)
        if result is None:
            return self._json({"error": "database unavailable"}, 503)
        messages, next_before = result
        return self._json({"messages": messages, "next_before": next_before})


    # ------------------------------------------------------------------ Telegram account API
    @staticmethod
    def _p_int(p: dict, name: str, default=None):
        v = p.get(name, default)
        if v in (None, ""):
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            raise AdminError(400, f"{name} number hona chahiye")

    @staticmethod
    def _p_bool(p: dict, name: str) -> bool:
        v = p.get(name)
        return v is True or (isinstance(v, str) and v.lower() in ("1", "true", "yes"))

    def _need_admin(self) -> TgAdmin:
        if self.admin is None:
            raise AdminError(503, "Telegram account API available nahi hai")
        return self.admin

    # AI controls (per chat / global) live in main.py - reached through `control`
    async def _ai_state(self, p: dict) -> dict:
        ctl = self.control
        if ctl is None:
            raise AdminError(503, "bot control available nahi hai")
        out = {"global": await ctl.ai_global_get(), "info": ctl.info()}
        if p.get("chat_id") not in (None, ""):
            out["chat"] = await ctl.ai_chat_get(self._p_int(p, "chat_id"))
        return out

    async def _ai_chat(self, p: dict) -> dict:
        ctl = self.control
        if ctl is None:
            raise AdminError(503, "bot control available nahi hai")
        saved = await ctl.ai_chat_set(self._p_int(p, "chat_id"), self._p_bool(p, "enabled"))
        return {"ok": True, "saved": bool(saved)}

    async def _ai_global(self, p: dict) -> dict:
        ctl = self.control
        if ctl is None:
            raise AdminError(503, "bot control available nahi hai")
        saved = await ctl.ai_global_set(self._p_bool(p, "enabled"))
        return {"ok": True, "saved": bool(saved)}

    def _get_ops(self):
        a = self._need_admin
        return {
            "me": lambda p: a().me(),
            "dialogs": lambda p: a().dialogs(p.get("filter", "all"), p.get("q", ""), self._p_int(p, "offset", 0), self._p_int(p, "limit", 60)),
            "find": lambda p: a().find(p.get("q", "")),
            "chat": lambda p: a().chat_info(p.get("chat_id")),
            "history": lambda p: a().history(p.get("chat_id"), self._p_int(p, "before", 0), self._p_int(p, "limit", 40)),
            "search": lambda p: a().search(p.get("chat_id"), p.get("q", "")),
            "username_check": lambda p: a().check_username(p.get("username", "")),
            "privacy": lambda p: a().privacy_get(),
            "blocked": lambda p: a().blocked_users(),
            "sessions": lambda p: a().sessions(),
            "ttl": lambda p: a().ttl_get(),
            "history_ttl": lambda p: a().history_ttl_get(),
            "notify": lambda p: a().notify_get(),
            "contacts": lambda p: a().contacts(p.get("q", "")),
            "ai": lambda p: self._ai_state(p),
        }

    def _post_ops(self):
        a = self._need_admin
        return {
            "profile": lambda p: a().update_profile(p.get("first_name"), p.get("last_name", ""), p.get("bio", "")),
            "username": lambda p: a().set_username(p.get("username", "")),
            "phone_request": lambda p: a().phone_request(p.get("phone", "")),
            "phone_confirm": lambda p: a().phone_confirm(p.get("phone", ""), p.get("code", "")),
            "photo_delete": lambda p: a().delete_photo(),
            "send": lambda p: a().send(p.get("chat_id"), p.get("text"), p.get("reply_to")),
            "edit": lambda p: a().edit(p.get("chat_id"), self._p_int(p, "message_id"), p.get("text")),
            "delete": lambda p: a().delete_messages(p.get("chat_id"), p.get("ids"), p.get("revoke", True) is not False),
            "forward": lambda p: a().forward(p.get("to_chat"), p.get("from_chat"), p.get("ids")),
            "read": lambda p: a().mark_read(p.get("chat_id")),
            "chat_action": lambda p: a().chat_action(p.get("chat_id"), p.get("action", ""), p.get("confirm") is True, self._p_bool(p, "revoke")),
            "privacy": lambda p: a().privacy_set(p.get("key", ""), p.get("value", ""), self._p_bool(p, "force")),
            "block": lambda p: a().block(p.get("user_id")),
            "unblock": lambda p: a().unblock(p.get("user_id")),
            "session_end": lambda p: a().terminate_session(p.get("hash"), p.get("confirm") is True),
            "sessions_end_others": lambda p: a().terminate_other_sessions(p.get("confirm") is True),
            "ttl": lambda p: a().ttl_set(self._p_int(p, "days")),
            "history_ttl": lambda p: a().history_ttl_set(self._p_int(p, "period")),
            "notify": lambda p: a().notify_set(p.get("scope", ""), self._p_bool(p, "muted"), p.get("previews") is not False),
            "contact_add": lambda p: a().add_contact(p.get("first_name"), p.get("last_name", ""), p.get("phone", ""), p.get("user", "")),
            "contact_delete": lambda p: a().delete_contact(p.get("user_id")),
            "ai_chat": lambda p: self._ai_chat(p),
            "ai_global": lambda p: self._ai_global(p),
        }

    async def _run_op(self, ops: dict, op: str, params: dict):
        fn = ops.get(op)
        if fn is None:
            return self._json({"error": "unknown operation"}, 404)
        try:
            return self._json(await fn(params))
        except AdminError as exc:
            return self._json({"error": exc.message}, exc.status)
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._json({"error": "internal error"}, 500)

    async def api_tg_get(self, request):
        blocked = self._guard(request)
        if blocked is not None:
            return blocked
        return await self._run_op(self._get_ops(), request.match_info.get("op", ""), dict(request.query))

    async def api_tg_post(self, request):
        blocked = self._guard(request, write=True)
        if blocked is not None:
            return blocked
        try:
            params = await request.json()
            if not isinstance(params, dict):
                raise ValueError
        except Exception:
            return self._json({"error": "bad json"}, 400)
        return await self._run_op(self._post_ops(), request.match_info.get("op", ""), params)

    async def api_upload(self, request):
        """multipart: kind=photo (profile photo) | file (send to chat: chat_id, caption, as_photo, reply_to)."""
        blocked = self._guard(request, write=True)
        if blocked is not None:
            return blocked
        kind = request.match_info.get("kind", "")
        try:
            form = await request.post()
            upload = form.get("file")
            data = upload.file.read() if upload is not None and hasattr(upload, "file") else b""
            name = getattr(upload, "filename", "") or "file"
            admin = self._need_admin()
            if kind == "photo":
                return self._json(await admin.set_photo(data, name))
            if kind == "file":
                return self._json(await admin.send_file(
                    form.get("chat_id"), data, name, str(form.get("caption", "")),
                    str(form.get("as_photo", "")).lower() in ("1", "true"), form.get("reply_to") or None))
            return self._json({"error": "unknown upload"}, 404)
        except AdminError as exc:
            return self._json({"error": exc.message}, exc.status)
        except Exception:
            return self._json({"error": "upload failed"}, 500)

    async def api_media(self, request):
        from aiohttp import web
        blocked = self._guard(request)
        if blocked is not None:
            return blocked
        try:
            msg_id = self._p_int(request.query, "id")
            if not msg_id:
                raise AdminError(400, "id chahiye")
            data, mime, name = await self._need_admin().media(request.query.get("chat_id"), msg_id,
                                                              request.query.get("thumb") in ("1", "true"))
        except AdminError as exc:
            return self._json({"error": exc.message}, exc.status)
        safe = "".join(ch for ch in name if ch.isalnum() or ch in "._- ")[:80] or "file"
        mime = mime.split(";")[0].strip().lower()
        # only plain pictures / video / audio may be shown inside the page; everything else (html, svg, pdf, apk ...)
        # is always a download with a neutral type, so a file sent to you can never run in the dashboard origin
        inline = mime in SAFE_INLINE and request.query.get("dl") not in ("1", "true")
        if mime not in SAFE_INLINE:
            mime = "application/octet-stream"
        headers = {**self._headers(app=True), "Cache-Control": "private, max-age=600",
                   "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{safe}"', "X-Content-Type-Options": "nosniff"}
        return web.Response(body=data, content_type=mime, headers=headers)

    async def api_avatar(self, request):
        from aiohttp import web
        blocked = self._guard(request)
        if blocked is not None:
            return blocked
        try:
            data = await self._need_admin().avatar(request.query.get("id"))
        except AdminError as exc:
            return self._json({"error": exc.message}, exc.status)
        if not data:
            return self._json({"error": "no photo"}, 404)
        return web.Response(body=data, content_type="image/jpeg", headers={**self._headers(app=True), "Cache-Control": "private, max-age=600"})

    async def api_events(self, request):
        from aiohttp import web
        blocked = self._guard(request)
        if blocked is not None:
            return blocked
        resp = web.StreamResponse(headers={**self._headers(app=True), "Content-Type": "text/event-stream", "X-Accel-Buffering": "no"})
        await resp.prepare(request)
        q = EVENTS.subscribe()
        try:
            await resp.write(b"retry: 5000\n\n")
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25)
                    await resp.write(("data: " + json.dumps(event) + "\n\n").encode())
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
        except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
            pass
        finally:
            EVENTS.unsubscribe(q)
        return resp


def setup(app, get_memory, control=None, password: str = ADMIN_PASSWORD) -> "Dashboard | None":
    """Register the /admin routes. Does nothing (no routes at all) when no ADMIN_PASSWORD is set.
    `control` (from main.py) gives access to the live Telegram client and the AI switches."""
    if not password:
        return None
    admin = TgAdmin(control.client) if control is not None else None
    dash = Dashboard(get_memory, password=password, control=control, admin=admin)
    app.router.add_get(BASE, dash.page)
    app.router.add_get(BASE + "/static/{name}", dash.static)
    app.router.add_post(BASE + "/login", dash.login)
    app.router.add_post(BASE + "/logout", dash.logout)
    app.router.add_get(BASE + "/api/users", dash.api_users)
    app.router.add_get(BASE + "/api/messages", dash.api_messages)
    app.router.add_get(BASE + "/api/tg/{op}", dash.api_tg_get)
    app.router.add_post(BASE + "/api/tg/{op}", dash.api_tg_post)
    app.router.add_post(BASE + "/api/upload/{kind}", dash.api_upload)
    app.router.add_get(BASE + "/api/media", dash.api_media)
    app.router.add_get(BASE + "/api/avatar", dash.api_avatar)
    app.router.add_get(BASE + "/api/events", dash.api_events)
    return dash
