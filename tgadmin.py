"""
Telegram account management API for the dashboard (STRING_SESSION / userbot mode only).

Every public method of `TgAdmin` returns plain JSON-able dicts/lists and raises `AdminError(status, message)`
for anything the user should see. Nothing here logs message text. Dangerous actions (delete chat, leave, clear history,
end sessions, change phone) need DASH_ALLOW_DANGEROUS and an explicit confirm flag from the page.

What it covers (same things as Telegram's own apps):
  chats      list by type (all / personal / bots / groups / channels / blocked / deleted), search, history, send, edit, delete,
             reply, upload photo/file, media + avatars, pin / mute / archive / mark unread / clear / delete / leave / block
  profile    first + last name, bio, username (with availability check), phone number (code confirmation), profile photo
  settings   privacy (last seen, phone, photo, bio, forwards, calls, invites, voice, find-by-number), blocked users,
             active sessions (devices), account self-destruct, notifications (private / groups / channels),
             default auto-delete timer, contacts
"""
import asyncio
import inspect
import io
import logging
import mimetypes
import re
import time
from datetime import datetime, timezone

from config import DASH_ALLOW_DANGEROUS, DASH_DIALOG_CACHE, DASH_MAX_DIALOGS, DASH_MEDIA_MAX_MB
from util import BoundedDict

log = logging.getLogger("girl-chatbot.tgadmin")

MEDIA_MAX = DASH_MEDIA_MAX_MB * 1024 * 1024
MUTE_FOREVER = 2147483647
FILTERS = ("all", "personal", "bots", "groups", "channels", "blocked", "deleted")


class AdminError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# ------------------------------------------------------------------ live events (SSE for the dashboard)
class EventBus:
    def __init__(self):
        self._queues: set = set()
        self._listeners: list = []

    def add_listener(self, fn) -> None:
        self._listeners.append(fn)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._queues.add(q)
        return q

    def unsubscribe(self, q) -> None:
        self._queues.discard(q)

    @property
    def subscribers(self) -> int:
        return len(self._queues)

    def publish(self, event: dict) -> None:
        for fn in self._listeners:
            try:
                fn(event)
            except Exception:
                pass
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:         # slow browser: drop, it will refresh by itself
                pass


EVENTS = EventBus()


# ------------------------------------------------------------------ pure helpers (unit-tested)
def iso(dt) -> "str | None":
    if dt is None:
        return None
    if isinstance(dt, (int, float)):
        if dt <= 0:
            return None
        dt = datetime.fromtimestamp(dt, tz=timezone.utc)
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def enum_name(value) -> str:
    return str(getattr(value, "name", value) or "").lower()


def ctype_of(chat) -> str:
    t = enum_name(getattr(chat, "type", ""))
    return t if t in ("private", "bot", "group", "supergroup", "channel") else "other"


def full_name(obj) -> str:
    parts = [getattr(obj, "first_name", None), getattr(obj, "last_name", None)]
    return " ".join(p for p in parts if p).strip()


def chat_title(chat, deleted: bool = False, me_id: "int | None" = None) -> str:
    if me_id is not None and getattr(chat, "id", None) == me_id:
        return "Saved Messages"
    if deleted:
        return "Deleted Account"
    return (getattr(chat, "title", None) or full_name(chat) or getattr(chat, "username", None) or "Unknown")


MEDIA_ATTRS = (("photo", "photo"), ("video", "video"), ("animation", "gif"), ("sticker", "sticker"), ("voice", "voice"),
               ("audio", "audio"), ("video_note", "round video"), ("document", "file"), ("contact", "contact"),
               ("location", "location"), ("venue", "venue"), ("poll", "poll"), ("dice", "dice"), ("game", "game"))


def media_of(m) -> "tuple[str, object] | tuple[None, None]":
    for attr, kind in MEDIA_ATTRS:
        obj = getattr(m, attr, None)
        if obj:
            return kind, obj
    return None, None


def sender_name(m) -> str:
    u = getattr(m, "from_user", None)
    if u is not None:
        return full_name(u) or getattr(u, "username", None) or "Unknown"
    sc = getattr(m, "sender_chat", None)
    if sc is not None:
        return getattr(sc, "title", None) or "Channel"
    return ""


def forward_name(m) -> "str | None":
    for attr in ("forward_sender_name",):
        v = getattr(m, attr, None)
        if v:
            return v
    ff, fc = getattr(m, "forward_from", None), getattr(m, "forward_from_chat", None)
    if ff is not None:
        return full_name(ff) or getattr(ff, "username", None) or "Unknown"
    if fc is not None:
        return getattr(fc, "title", None) or "Channel"
    origin = getattr(m, "forward_origin", None)
    if origin is not None:
        for sub in ("sender_user", "chat", "sender_chat"):
            o = getattr(origin, sub, None)
            if o is not None:
                return getattr(o, "title", None) or full_name(o) or "Unknown"
        return getattr(origin, "sender_user_name", None) or "Unknown"
    return None


def serialize_message(m, chat_id: "int | None" = None) -> dict:
    kind, obj = media_of(m)
    text = getattr(m, "text", None) or getattr(m, "caption", None) or ""
    service = getattr(m, "service", None)
    if service:
        text = enum_name(service).replace("_", " ")
        kind = kind or "service"
    out = {
        "id": m.id,
        "chat_id": chat_id if chat_id is not None else getattr(getattr(m, "chat", None), "id", None),
        "date": iso(getattr(m, "date", None)),
        "out": bool(getattr(m, "outgoing", False)),
        "from": sender_name(m),
        "from_id": getattr(getattr(m, "from_user", None), "id", None),
        "text": str(text),
        "kind": kind,
        "service": bool(service),
        "reply_to": getattr(m, "reply_to_message_id", None),
        "fwd": forward_name(m),
        "edited": iso(getattr(m, "edit_date", None)),
        "views": getattr(m, "views", None),
    }
    if obj is not None:
        if kind in ("photo", "video", "gif", "sticker", "voice", "audio", "round video", "file"):
            out["file_name"] = getattr(obj, "file_name", None) or ""
            out["size"] = getattr(obj, "file_size", None)
            out["mime"] = getattr(obj, "mime_type", None)
            out["duration"] = getattr(obj, "duration", None)
            out["width"] = getattr(obj, "width", None)
            out["height"] = getattr(obj, "height", None)
            if kind == "sticker":
                out["emoji"] = getattr(obj, "emoji", None)
                out["animated"] = bool(getattr(obj, "is_animated", False))
            if kind == "audio":
                out["title"] = getattr(obj, "title", None)
                out["performer"] = getattr(obj, "performer", None)
        elif kind == "contact":
            out["contact"] = {"name": full_name(obj), "phone": getattr(obj, "phone_number", None)}
        elif kind in ("location", "venue"):
            loc = getattr(obj, "location", obj)
            out["location"] = {"lat": getattr(loc, "latitude", None), "lon": getattr(loc, "longitude", None),
                               "title": getattr(obj, "title", None)}
        elif kind == "poll":
            out["poll"] = {"question": getattr(obj, "question", ""),
                           "options": [getattr(o, "text", "") for o in getattr(obj, "options", [])]}
        elif kind == "dice":
            out["emoji"] = getattr(obj, "emoji", None)
            out["value"] = getattr(obj, "value", None)
    return out


def last_preview(m, me_id: "int | None" = None) -> dict:
    if m is None:
        return {"text": "", "date": None, "out": False, "from": ""}
    s = serialize_message(m)
    label = {"photo": "🖼 Photo", "video": "🎬 Video", "gif": "GIF", "sticker": f"{s.get('emoji') or ''} Sticker",
             "voice": "🎤 Voice message", "audio": "🎵 Audio", "round video": "⏺ Video message", "file": "📎 " + (s.get("file_name") or "File"),
             "contact": "👤 Contact", "location": "📍 Location", "venue": "📍 Location", "poll": "📊 Poll",
             "dice": "🎲 Dice", "game": "🎮 Game"}.get(s["kind"] or "", "")
    text = s["text"] if (s["text"] and s["kind"] != "service") else (label or s["text"])
    if s["text"] and label and s["kind"] not in (None, "service"):
        text = f"{label} {s['text']}"
    return {"text": text[:140], "date": s["date"], "out": s["out"], "from": s["from"], "id": s["id"]}


def category_ok(item: dict, flt: str) -> bool:
    t = item["type"]
    if flt in ("", "all"):
        return True
    if flt == "personal":
        return t == "private" and not item["deleted"] and not item["blocked"]
    if flt == "bots":
        return t == "bot"
    if flt == "groups":
        return t in ("group", "supergroup")
    if flt == "channels":
        return t == "channel"
    if flt == "blocked":
        return item["blocked"]
    if flt == "deleted":
        return item["deleted"]
    return False


def serialize_dialog(d, info: "dict | None", blocked: set, me_id: "int | None") -> dict:
    chat = d.chat
    ctype = ctype_of(chat)
    info = info or {}
    deleted = bool(info.get("deleted"))
    photo = getattr(chat, "photo", None)
    return {
        "id": chat.id,
        "type": ctype,
        "title": chat_title(chat, deleted, me_id),
        "username": getattr(chat, "username", None),
        "saved": me_id is not None and chat.id == me_id,
        "unread": int(getattr(d, "unread_messages_count", 0) or 0),
        "unread_mark": bool(getattr(d, "unread_mark", False)),
        "mentions": int(getattr(d, "unread_mentions_count", 0) or 0),
        "pinned": bool(getattr(d, "is_pinned", False)),
        "avatar": bool(photo),
        "verified": bool(getattr(chat, "is_verified", False)),
        "deleted": deleted,
        "blocked": chat.id in blocked,
        "contact": bool(info.get("contact")),
        "status": info.get("status"),
        "last": last_preview(getattr(d, "top_message", None), me_id),
    }


PRIVACY_KEYS = {
    "last_seen": ("InputPrivacyKeyStatusTimestamp", "Last seen & online"),
    "phone": ("InputPrivacyKeyPhoneNumber", "Phone number"),
    "find_by_phone": ("InputPrivacyKeyAddedByPhone", "Who can find me by my number"),
    "photo": ("InputPrivacyKeyProfilePhoto", "Profile photos"),
    "bio": ("InputPrivacyKeyAbout", "Bio"),
    "forwards": ("InputPrivacyKeyForwards", "Forwarded messages"),
    "calls": ("InputPrivacyKeyPhoneCall", "Calls"),
    "p2p": ("InputPrivacyKeyPhoneP2P", "Peer-to-peer in calls"),
    "invites": ("InputPrivacyKeyChatInvite", "Groups & channels invites"),
    "voice": ("InputPrivacyKeyVoiceMessages", "Voice messages"),
    "birthday": ("InputPrivacyKeyBirthday", "Date of birth"),
}
PRIVACY_OPTIONS = ("everybody", "contacts", "nobody")


def privacy_from_rules(rules) -> dict:
    """Telegram rule list -> {'value': everybody|contacts|nobody, 'exceptions': n}."""
    value, exceptions = "nobody", 0
    for r in rules or []:
        name = type(r).__name__
        if name == "PrivacyValueAllowAll":
            value = "everybody"
        elif name == "PrivacyValueAllowContacts" and value != "everybody":
            value = "contacts"
        elif name in ("PrivacyValueAllowUsers", "PrivacyValueDisallowUsers"):
            exceptions += len(getattr(r, "users", []) or [])
        elif name in ("PrivacyValueAllowChatParticipants", "PrivacyValueDisallowChatParticipants"):
            exceptions += len(getattr(r, "chats", []) or [])
    return {"value": value, "exceptions": exceptions}


def parse_phone(raw_phone: str) -> str:
    digits = re.sub(r"[^\d]", "", raw_phone or "")
    if not 7 <= len(digits) <= 15:
        raise AdminError(400, "Phone number galat hai (country code ke saath likho, jaise +919876543210)")
    return "+" + digits


# ------------------------------------------------------------------ the API
class TgAdmin:
    def __init__(self, get_client, max_dialogs: int = DASH_MAX_DIALOGS, cache_ttl: int = DASH_DIALOG_CACHE,
                 allow_dangerous: bool = DASH_ALLOW_DANGEROUS, min_reload: float = 5.0):
        self.get_client = get_client
        self.max_dialogs = max_dialogs
        self.cache_ttl = cache_ttl
        self.min_reload = min_reload             # a busy account must not make us re-read all chats on every message
        self._dirty = False
        self.allow_dangerous = allow_dangerous
        self._dialogs = (0.0, None)              # (loaded_at, items)
        self._blocked = (0.0, None)              # (loaded_at, list of user dicts)
        self._users = BoundedDict(3000)          # user id -> (ts, info)
        self._avatars = BoundedDict(300)         # chat id -> (ts, bytes|None)
        self._photo_ids = BoundedDict(3000)      # chat id -> small_file_id
        self._phone_pending: "dict | None" = None
        self._lock = asyncio.Lock()
        EVENTS.add_listener(self._on_event)

    # ---- plumbing
    def _on_event(self, event: dict) -> None:
        self._dirty = True

    def invalidate(self) -> None:
        self._dirty = True
        self._blocked = (0.0, self._blocked[1])

    def _fresh(self, loaded: float, items) -> bool:
        if items is None:
            return False
        age = time.time() - loaded
        return age < self.min_reload or (age < self.cache_ttl and not self._dirty)

    def client(self):
        c = self.get_client()
        if c is None or not getattr(c, "is_connected", False):
            raise AdminError(503, "Telegram abhi connected nahi hai, thodi der baad try karo")
        return c

    async def _call(self, coro):
        try:
            return await coro
        except AdminError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            name = type(exc).__name__
            if name == "FloodWait":
                secs = getattr(exc, "value", None) or getattr(exc, "x", None) or 5
                raise AdminError(429, f"Telegram ne rate limit lagayi hai - {secs} second ruko") from exc
            code = getattr(exc, "ID", None)
            if isinstance(code, str) and code:
                raise AdminError(400, f"Telegram: {code}") from exc
            log.warning("dashboard action failed (%s)", name)
            raise AdminError(500, f"Action fail hua ({name})") from exc

    def _need_dangerous(self, confirm) -> None:
        if not self.allow_dangerous:
            raise AdminError(403, "Ye action DASH_ALLOW_DANGEROUS=0 se band hai")
        if confirm is not True:
            raise AdminError(400, "Confirm zaroori hai")

    @staticmethod
    def _chat_id(value) -> "int | str":
        if isinstance(value, bool):
            raise AdminError(400, "bad chat id")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip():
            v = value.strip()
            if re.fullmatch(r"-?\d+", v):
                return int(v)
            if v == "me" or re.fullmatch(r"@?[A-Za-z][A-Za-z0-9_]{3,31}", v):
                return v.lstrip("@") if v != "me" else "me"
        raise AdminError(400, "bad chat id")

    @staticmethod
    def _ids(ids) -> list:
        if (not isinstance(ids, list) or not ids or len(ids) > 100
                or not all(isinstance(i, int) and not isinstance(i, bool) and i > 0 for i in ids)):
            raise AdminError(400, "message ids galat hain")
        return ids

    @staticmethod
    def _text(value, limit: int = 4096, allow_empty: bool = False) -> str:
        if not isinstance(value, str):
            raise AdminError(400, "text chahiye")
        if not allow_empty and not value.strip():
            raise AdminError(400, "text khali hai")
        if len(value) > limit:
            raise AdminError(400, f"text {limit} characters se zyada nahi ho sakta")
        return value

    @staticmethod
    def _reply_kw(client, reply_to) -> dict:
        if not reply_to:
            return {}
        try:
            params = inspect.signature(client.send_message).parameters
        except (TypeError, ValueError):
            return {}
        if "reply_parameters" in params:
            from pyrogram.types import ReplyParameters
            return {"reply_parameters": ReplyParameters(message_id=int(reply_to))}
        if "reply_to_message_id" in params:
            return {"reply_to_message_id": int(reply_to)}
        return {}

    @staticmethod
    def _bio(data: bytes, name: str):
        bio = io.BytesIO(data)
        bio.name = re.sub(r"[^\w.\- ]", "_", name or "file")[:100] or "file"
        return bio

    # ---- me / profile
    async def me(self) -> dict:
        c = self.client()
        u = await self._call(c.get_me())
        bio = ""
        try:
            chat = await c.get_chat("me")
            bio = getattr(chat, "bio", None) or ""
        except Exception:
            pass
        return {"id": u.id, "first_name": u.first_name or "", "last_name": getattr(u, "last_name", None) or "",
                "username": getattr(u, "username", None) or "", "phone": getattr(u, "phone_number", None) or "",
                "bio": bio, "premium": bool(getattr(u, "is_premium", False)), "has_photo": bool(getattr(u, "photo", None))}

    async def update_profile(self, first_name, last_name, bio) -> dict:
        first = self._text(first_name, 64)
        last = self._text(last_name or "", 64, allow_empty=True)
        about = self._text(bio or "", 140, allow_empty=True)
        await self._call(self.client().update_profile(first_name=first.strip(), last_name=last.strip(), bio=about.strip()))
        self._photo_ids.pop(self._me_id_cache(), None)
        return await self.me()

    def _me_id_cache(self):
        c = self.get_client()
        return getattr(getattr(c, "me", None), "id", None)

    async def check_username(self, username: str) -> dict:
        from pyrogram import raw
        name = (username or "").strip().lstrip("@")
        if name == "":
            return {"available": True, "empty": True}
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", name):
            return {"available": False, "reason": "5-32 characters, letters/numbers/_ , letter se shuru"}
        ok = await self._call(self.client().invoke(raw.functions.account.CheckUsername(username=name)))
        return {"available": bool(ok)}

    async def set_username(self, username: str) -> dict:
        name = (username or "").strip().lstrip("@")
        if name and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", name):
            raise AdminError(400, "Username 5-32 characters ka ho (letters, numbers, _), letter se shuru")
        await self._call(self.client().set_username(name or None))
        return await self.me()

    async def phone_request(self, phone: str) -> dict:
        from pyrogram import raw
        number = parse_phone(phone)
        sent = await self._call(self.client().invoke(
            raw.functions.account.SendChangePhoneCode(phone_number=number, settings=raw.types.CodeSettings())))
        self._phone_pending = {"phone": number, "hash": sent.phone_code_hash, "ts": time.time()}
        return {"sent": True, "phone": number, "timeout": getattr(sent, "timeout", None)}

    async def phone_confirm(self, phone: str, code: str) -> dict:
        from pyrogram import raw
        p = self._phone_pending
        number = parse_phone(phone)
        if not p or p["phone"] != number or time.time() - p["ts"] > 600:
            raise AdminError(400, "Pehle code mangao (code 10 minute tak chalta hai)")
        code = re.sub(r"\D", "", code or "")
        if not 4 <= len(code) <= 8:
            raise AdminError(400, "Code galat hai")
        await self._call(self.client().invoke(raw.functions.account.ChangePhone(
            phone_number=number, phone_code_hash=p["hash"], phone_code=code)))
        self._phone_pending = None
        return await self.me()

    async def set_photo(self, data: bytes, filename: str) -> dict:
        if not data or len(data) > MEDIA_MAX:
            raise AdminError(413, f"Photo {DASH_MEDIA_MAX_MB} MB se chhoti honi chahiye")
        await self._call(self.client().set_profile_photo(photo=self._bio(data, filename or "photo.jpg")))
        self._avatars.pop(self._me_id_cache(), None)
        return await self.me()

    async def delete_photo(self) -> dict:
        c = self.client()
        first = None
        async for p in c.get_chat_photos("me", limit=1):
            first = p
            break
        if first is None:
            raise AdminError(404, "Profile photo nahi hai")
        await self._call(c.delete_profile_photos(first.file_id))
        self._avatars.pop(self._me_id_cache(), None)
        return await self.me()

    # ---- users info / blocked
    async def _users_info(self, ids: list) -> dict:
        now = time.time()
        out, need = {}, []
        for i in ids:
            hit = self._users.get(i)
            if hit and now - hit[0] < 300:
                out[i] = hit[1]
            else:
                need.append(i)
        c = self.get_client()
        for k in range(0, len(need), 100):
            chunk = need[k:k + 100]
            try:
                users = await c.get_users(chunk)
            except Exception:
                continue
            if not isinstance(users, list):
                users = [users]
            for u in users:
                info = {"deleted": bool(getattr(u, "is_deleted", False)), "contact": bool(getattr(u, "is_contact", False)),
                        "bot": bool(getattr(u, "is_bot", False)), "status": enum_name(getattr(u, "status", None)) or None}
                self._users[u.id] = (now, info)
                out[u.id] = info
        return out

    async def _blocked_users(self, force: bool = False) -> list:
        loaded, items = self._blocked
        if items is not None and not force and time.time() - loaded < 60:
            return items
        from pyrogram import raw
        c = self.client()
        out, offset = [], 0
        for _ in range(10):                                  # up to 1000 blocked users
            res = await self._call(c.invoke(raw.functions.contacts.GetBlocked(offset=offset, limit=100)))
            users = {u.id: u for u in getattr(res, "users", [])}
            batch = getattr(res, "blocked", [])
            for b in batch:
                uid = getattr(getattr(b, "peer_id", None), "user_id", None)
                u = users.get(uid)
                if uid is None:
                    continue
                out.append({"id": uid, "name": (full_name(u) if u else "") or "Unknown",
                            "username": getattr(u, "username", None) if u else None,
                            "deleted": bool(getattr(u, "deleted", False)) if u else False})
            if len(batch) < 100:
                break
            offset += len(batch)
        self._blocked = (time.time(), out)
        return out

    async def blocked_users(self) -> dict:
        items = await self._blocked_users(force=True)
        return {"items": items, "count": len(items)}

    async def block(self, user_id) -> dict:
        await self._call(self.client().block_user(self._chat_id(user_id)))
        self.invalidate()
        return {"ok": True}

    async def unblock(self, user_id) -> dict:
        await self._call(self.client().unblock_user(self._chat_id(user_id)))
        self.invalidate()
        return {"ok": True}

    # ---- dialogs
    async def _load_dialogs(self) -> list:
        loaded, items = self._dialogs
        if self._fresh(loaded, items):
            return items
        async with self._lock:
            loaded, items = self._dialogs
            if self._fresh(loaded, items):
                return items
            self._dirty = False
            c = self.client()
            raw_dialogs = []
            async for d in c.get_dialogs(limit=self.max_dialogs):
                raw_dialogs.append(d)
            me_id = getattr(getattr(c, "me", None), "id", None)
            ids = [d.chat.id for d in raw_dialogs if ctype_of(d.chat) in ("private", "bot")]
            info = await self._users_info(ids) if ids else {}
            try:
                blocked_ids = {b["id"] for b in await self._blocked_users()}
            except AdminError:
                blocked_ids = set()
            items = []
            for d in raw_dialogs:
                try:
                    items.append(serialize_dialog(d, info.get(d.chat.id), blocked_ids, me_id))
                    photo = getattr(d.chat, "photo", None)
                    if photo is not None:
                        self._photo_ids[d.chat.id] = getattr(photo, "small_file_id", None)
                except Exception:
                    log.warning("skipped one dialog (could not parse)")
            self._dialogs = (time.time(), items)
            return items

    async def dialogs(self, flt: str = "all", q: str = "", offset: int = 0, limit: int = 60) -> dict:
        if flt not in FILTERS:
            raise AdminError(400, "bad filter")
        items = await self._load_dialogs()
        counts = {f: sum(1 for i in items if category_ok(i, f)) for f in FILTERS}
        rows = [i for i in items if category_ok(i, flt)]
        try:
            blocked_all = await self._blocked_users()
        except AdminError:
            blocked_all = []
        counts["blocked"] = len({b["id"] for b in blocked_all} | {i["id"] for i in items if i["blocked"]})
        if flt == "blocked":                                   # blocked people without any chat are still listed
            have = {i["id"] for i in rows}
            for b in blocked_all:
                if b["id"] not in have:
                    rows.append({"id": b["id"], "type": "private", "title": b["name"] if not b["deleted"] else "Deleted Account",
                                 "username": b["username"], "saved": False, "unread": 0, "unread_mark": False, "mentions": 0,
                                 "pinned": False, "avatar": False, "verified": False, "deleted": b["deleted"], "blocked": True,
                                 "contact": False, "status": None, "last": {"text": "", "date": None, "out": False, "from": ""}})
        needle = (q or "").strip().lower()
        if needle:
            rows = [i for i in rows if needle in i["title"].lower() or needle in (i["username"] or "").lower()
                    or needle in i["last"]["text"].lower()]
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        page = rows[offset:offset + limit]
        return {"items": page, "total": len(rows), "next": offset + limit if offset + limit < len(rows) else None, "counts": counts}

    async def find(self, q: str) -> dict:
        """People / chats not in the chat list (Telegram global search + @username lookup)."""
        from pyrogram import raw
        needle = (q or "").strip()
        if len(needle) < 3:
            return {"items": []}
        res = await self._call(self.client().invoke(raw.functions.contacts.Search(q=needle.lstrip("@"), limit=15)))
        items = []
        for u in getattr(res, "users", []):
            items.append({"id": u.id, "type": "bot" if getattr(u, "bot", False) else "private",
                          "title": full_name(u) or getattr(u, "username", None) or "Unknown", "username": getattr(u, "username", None)})
        for ch in getattr(res, "chats", []):
            items.append({"id": int(f"-100{ch.id}") if getattr(ch, "broadcast", False) or getattr(ch, "megagroup", False) else -ch.id,
                          "type": "channel" if getattr(ch, "broadcast", False) else "supergroup",
                          "title": getattr(ch, "title", "Unknown"), "username": getattr(ch, "username", None)})
        return {"items": items[:20]}

    async def chat_info(self, chat_id) -> dict:
        c = self.client()
        cid = self._chat_id(chat_id)
        chat = await self._call(c.get_chat(cid))
        ctype = ctype_of(chat)
        me_id = getattr(getattr(c, "me", None), "id", None)
        info = {}
        if ctype in ("private", "bot"):
            info = (await self._users_info([chat.id])).get(chat.id, {})
        blocked_ids = {b["id"] for b in (self._blocked[1] or [])}
        out = {
            "id": chat.id, "type": ctype, "title": chat_title(chat, bool(info.get("deleted")), me_id),
            "username": getattr(chat, "username", None), "bio": getattr(chat, "bio", None) or "",
            "description": getattr(chat, "description", None) or "", "members": getattr(chat, "members_count", None),
            "verified": bool(getattr(chat, "is_verified", False)), "deleted": bool(info.get("deleted")),
            "contact": bool(info.get("contact")), "status": info.get("status"), "blocked": chat.id in blocked_ids,
            "saved": chat.id == me_id, "muted": None, "avatar": bool(getattr(chat, "photo", None)),
            "phone": None,
        }
        if getattr(chat, "photo", None) is not None:
            self._photo_ids[chat.id] = getattr(chat.photo, "small_file_id", None)
        try:
            from pyrogram import raw
            peer = await c.resolve_peer(chat.id)
            ns = await c.invoke(raw.functions.account.GetNotifySettings(peer=raw.types.InputNotifyPeer(peer=peer)))
            until = getattr(ns, "mute_until", None) or 0
            out["muted"] = bool(until and until > time.time())
        except Exception:
            pass
        return out

    # ---- messages
    async def history(self, chat_id, before_id: int = 0, limit: int = 40) -> dict:
        c = self.client()
        cid = self._chat_id(chat_id)
        limit = max(1, min(int(limit or 40), 100))
        msgs = []
        async def gather():
            async for m in c.get_chat_history(cid, limit=limit, offset_id=int(before_id or 0)):
                msgs.append(serialize_message(m, cid if isinstance(cid, int) else None))
        await self._call(gather())
        msgs.reverse()                                          # oldest first
        return {"messages": msgs, "has_more": len(msgs) >= limit}

    async def search(self, chat_id, q: str) -> dict:
        c = self.client()
        cid = self._chat_id(chat_id)
        q = self._text(q, 100)
        out = []
        async def gather():
            async for m in c.search_messages(cid, query=q, limit=30):
                out.append(serialize_message(m, cid if isinstance(cid, int) else None))
        await self._call(gather())
        return {"messages": out}

    async def send(self, chat_id, text, reply_to=None) -> dict:
        c = self.client()
        m = await self._call(c.send_message(self._chat_id(chat_id), self._text(text), **self._reply_kw(c, reply_to)))
        return {"message": serialize_message(m)}

    async def send_file(self, chat_id, data: bytes, filename: str, caption: str = "", as_photo: bool = False, reply_to=None) -> dict:
        if not data:
            raise AdminError(400, "file khali hai")
        if len(data) > MEDIA_MAX:
            raise AdminError(413, f"File {DASH_MEDIA_MAX_MB} MB se badi hai")
        c = self.client()
        cid = self._chat_id(chat_id)
        caption = self._text(caption or "", 1024, allow_empty=True)
        bio = self._bio(data, filename)
        mime = mimetypes.guess_type(bio.name)[0] or ""
        kw = self._reply_kw(c, reply_to)
        if as_photo and mime.startswith("image/"):
            m = await self._call(c.send_photo(cid, bio, caption=caption, **kw))
        else:
            m = await self._call(c.send_document(cid, bio, caption=caption, file_name=bio.name, **kw))
        return {"message": serialize_message(m)}

    async def edit(self, chat_id, message_id, text) -> dict:
        m = await self._call(self.client().edit_message_text(self._chat_id(chat_id), int(message_id), self._text(text)))
        return {"message": serialize_message(m)}

    async def delete_messages(self, chat_id, ids, revoke: bool = True) -> dict:
        ids = self._ids(ids)
        await self._call(self.client().delete_messages(self._chat_id(chat_id), ids, revoke=bool(revoke)))
        self.invalidate()
        return {"ok": True}

    async def forward(self, to_chat, from_chat, ids) -> dict:
        ids = self._ids(ids)
        await self._call(self.client().forward_messages(chat_id=self._chat_id(to_chat), from_chat_id=self._chat_id(from_chat), message_ids=ids))
        return {"ok": True}

    async def mark_read(self, chat_id) -> dict:
        await self._call(self.client().read_chat_history(self._chat_id(chat_id)))
        self.invalidate()
        return {"ok": True}

    async def media(self, chat_id, message_id: int, thumb: bool = False) -> "tuple[bytes, str, str]":
        c = self.client()
        cid = self._chat_id(chat_id)
        m = await self._call(c.get_messages(cid, int(message_id)))
        if not m or getattr(m, "empty", False):
            raise AdminError(404, "message nahi mila")
        kind, obj = media_of(m)
        if obj is None or kind in ("contact", "location", "venue", "poll", "dice", "game"):
            raise AdminError(404, "is message me file nahi hai")
        size = getattr(obj, "file_size", 0) or 0
        target, name = m, getattr(obj, "file_name", None) or f"{kind}_{m.id}"
        if thumb and kind in ("video", "gif", "round video", "file", "audio"):
            thumbs = getattr(obj, "thumbs", None) or []
            if not thumbs:
                raise AdminError(404, "thumbnail nahi hai")
            target, size = thumbs[-1].file_id, getattr(thumbs[-1], "file_size", 0) or 0
        if size > MEDIA_MAX:
            raise AdminError(413, f"File {DASH_MEDIA_MAX_MB} MB se badi hai")
        buf = await self._call(c.download_media(target, in_memory=True))
        if buf is None:
            raise AdminError(404, "download nahi hua")
        data = buf.getvalue() if hasattr(buf, "getvalue") else bytes(buf)
        mime = getattr(obj, "mime_type", None) or mimetypes.guess_type(name)[0] or "application/octet-stream"
        if kind == "photo" or (thumb and target is not m):
            mime = "image/jpeg"
        elif kind == "sticker" and not getattr(obj, "mime_type", None):
            mime = "image/webp"
        return data, mime, name

    async def avatar(self, chat_id) -> "bytes | None":
        c = self.client()
        cid = self._chat_id(chat_id)
        hit = self._avatars.get(cid)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        file_id = self._photo_ids.get(cid)
        if not file_id:
            try:
                chat = await c.get_chat(cid)
                photo = getattr(chat, "photo", None)
                file_id = getattr(photo, "small_file_id", None) if photo else None
            except Exception:
                file_id = None
        data = None
        if file_id:
            try:
                buf = await c.download_media(file_id, in_memory=True)
                data = buf.getvalue() if buf is not None else None
            except Exception:
                data = None
        self._avatars[cid] = (time.time(), data)
        return data

    # ---- chat actions
    async def chat_action(self, chat_id, action: str, confirm=False, revoke=False) -> dict:
        from pyrogram import raw
        c = self.client()
        cid = self._chat_id(chat_id)
        if action in ("pin", "unpin", "mute", "unmute", "mark_unread", "clear", "delete"):
            peer = await self._call(c.resolve_peer(cid))
        if action in ("pin", "unpin"):
            await self._call(c.invoke(raw.functions.messages.ToggleDialogPin(
                peer=raw.types.InputDialogPeer(peer=peer), pinned=action == "pin")))
        elif action in ("mute", "unmute"):
            await self._call(c.invoke(raw.functions.account.UpdateNotifySettings(
                peer=raw.types.InputNotifyPeer(peer=peer),
                settings=raw.types.InputPeerNotifySettings(mute_until=MUTE_FOREVER if action == "mute" else 0))))
        elif action == "mark_unread":
            await self._call(c.invoke(raw.functions.messages.MarkDialogUnread(
                peer=raw.types.InputDialogPeer(peer=peer), unread=True)))
        elif action == "archive":
            await self._call(c.archive_chats(cid))
        elif action == "unarchive":
            await self._call(c.unarchive_chats(cid))
        elif action == "block":
            await self._call(c.block_user(cid))
        elif action == "unblock":
            await self._call(c.unblock_user(cid))
        elif action in ("clear", "delete"):
            self._need_dangerous(confirm)
            for _ in range(20):                                 # DeleteHistory works in chunks
                res = await self._call(c.invoke(raw.functions.messages.DeleteHistory(
                    peer=peer, max_id=0, just_clear=action == "clear", revoke=bool(revoke))))
                if not getattr(res, "offset", 0):
                    break
        elif action == "leave":
            self._need_dangerous(confirm)
            await self._call(c.leave_chat(cid, delete=bool(revoke)))
        else:
            raise AdminError(400, "unknown action")
        self.invalidate()
        return {"ok": True}

    # ---- privacy
    def _privacy_key(self, name: str):
        from pyrogram import raw
        spec = PRIVACY_KEYS.get(name)
        cls = getattr(raw.types, spec[0], None) if spec else None
        if cls is None:
            raise AdminError(400, "ye privacy option is Telegram version me nahi hai")
        return cls()

    async def privacy_get(self) -> dict:
        from pyrogram import raw
        c = self.client()
        items = []
        for name, (cls_name, label) in PRIVACY_KEYS.items():
            if getattr(raw.types, cls_name, None) is None:
                continue
            try:
                res = await c.invoke(raw.functions.account.GetPrivacy(key=self._privacy_key(name)))
            except Exception:
                continue
            p = privacy_from_rules(getattr(res, "rules", []))
            items.append({"key": name, "label": label, **p})
        return {"items": items, "options": list(PRIVACY_OPTIONS)}

    async def privacy_set(self, key: str, value: str, force: bool = False) -> dict:
        from pyrogram import raw
        if value not in PRIVACY_OPTIONS:
            raise AdminError(400, "bad value")
        if key == "find_by_phone" and value == "nobody":
            raise AdminError(400, "Number se dhundhne ke liye sirf Everybody ya My Contacts hota hai")
        c = self.client()
        cur = await self._call(c.invoke(raw.functions.account.GetPrivacy(key=self._privacy_key(key))))
        existing = privacy_from_rules(getattr(cur, "rules", []))
        if existing["exceptions"] and not force:
            raise AdminError(409, f"Is setting me {existing['exceptions']} exceptions hain; badalne se wo hat jayengi. Confirm karke dobara bhejo")
        rule = {"everybody": raw.types.InputPrivacyValueAllowAll, "contacts": raw.types.InputPrivacyValueAllowContacts,
                "nobody": raw.types.InputPrivacyValueDisallowAll}[value]()
        await self._call(c.invoke(raw.functions.account.SetPrivacy(key=self._privacy_key(key), rules=[rule])))
        return {"ok": True, "key": key, "value": value}

    # ---- sessions (devices)
    async def sessions(self) -> dict:
        from pyrogram import raw
        res = await self._call(self.client().invoke(raw.functions.account.GetAuthorizations()))
        items = []
        for a in getattr(res, "authorizations", []):
            items.append({"hash": str(getattr(a, "hash", 0)), "current": bool(getattr(a, "current", False)),
                          "device": getattr(a, "device_model", "") or "", "platform": getattr(a, "platform", "") or "",
                          "system": getattr(a, "system_version", "") or "", "app": f"{getattr(a, 'app_name', '')} {getattr(a, 'app_version', '')}".strip(),
                          "ip": getattr(a, "ip", "") or "", "country": getattr(a, "country", "") or "",
                          "region": getattr(a, "region", "") or "", "created": iso(getattr(a, "date_created", None)),
                          "active": iso(getattr(a, "date_active", None)), "official": bool(getattr(a, "official_app", False))})
        items.sort(key=lambda x: (not x["current"], x["active"] or ""), reverse=False)
        return {"items": items}

    async def terminate_session(self, session_hash, confirm=False) -> dict:
        from pyrogram import raw
        self._need_dangerous(confirm)
        try:
            h = int(session_hash)
        except (TypeError, ValueError):
            raise AdminError(400, "bad session")
        current = [s for s in (await self.sessions())["items"] if s["current"]]
        if h == 0 or any(int(s["hash"]) == h for s in current):
            raise AdminError(400, "Ye wahi session hai jisse bot chal raha hai, ise band nahi kar sakte")
        await self._call(self.client().invoke(raw.functions.account.ResetAuthorization(hash=h)))
        return {"ok": True}

    async def terminate_other_sessions(self, confirm=False) -> dict:
        from pyrogram import raw
        self._need_dangerous(confirm)
        n = 0
        for s in (await self.sessions())["items"]:
            if s["current"]:
                continue
            await self._call(self.client().invoke(raw.functions.account.ResetAuthorization(hash=int(s["hash"]))))
            n += 1
        return {"ok": True, "terminated": n}

    # ---- account TTL, default auto-delete, notifications
    async def ttl_get(self) -> dict:
        from pyrogram import raw
        res = await self._call(self.client().invoke(raw.functions.account.GetAccountTTL()))
        return {"days": getattr(res, "days", 365), "options": [30, 90, 180, 365]}

    async def ttl_set(self, days: int) -> dict:
        from pyrogram import raw
        if days not in (30, 90, 180, 365):
            raise AdminError(400, "days 30, 90, 180 ya 365 hone chahiye")
        await self._call(self.client().invoke(raw.functions.account.SetAccountTTL(ttl=raw.types.AccountDaysTTL(days=days))))
        return {"ok": True, "days": days}

    async def history_ttl_get(self) -> dict:
        from pyrogram import raw
        res = await self._call(self.client().invoke(raw.functions.messages.GetDefaultHistoryTTL()))
        return {"period": getattr(res, "period", 0), "options": [0, 86400, 604800, 2678400]}

    async def history_ttl_set(self, period: int) -> dict:
        from pyrogram import raw
        if period not in (0, 86400, 604800, 2678400):
            raise AdminError(400, "period galat hai")
        await self._call(self.client().invoke(raw.functions.messages.SetDefaultHistoryTTL(period=period)))
        return {"ok": True, "period": period}

    SCOPES = {"private": "InputNotifyUsers", "groups": "InputNotifyChats", "channels": "InputNotifyBroadcasts"}

    async def notify_get(self) -> dict:
        from pyrogram import raw
        c = self.client()
        items = []
        for scope, cls_name in self.SCOPES.items():
            try:
                ns = await c.invoke(raw.functions.account.GetNotifySettings(peer=getattr(raw.types, cls_name)()))
            except Exception:
                continue
            until = getattr(ns, "mute_until", None) or 0
            items.append({"scope": scope, "muted": bool(until and until > time.time()),
                          "previews": getattr(ns, "show_previews", None) is not False})
        return {"items": items}

    async def notify_set(self, scope: str, muted: bool, previews: bool) -> dict:
        from pyrogram import raw
        cls_name = self.SCOPES.get(scope)
        if not cls_name:
            raise AdminError(400, "bad scope")
        settings = raw.types.InputPeerNotifySettings(show_previews=bool(previews), mute_until=MUTE_FOREVER if muted else 0)
        await self._call(self.client().invoke(raw.functions.account.UpdateNotifySettings(
            peer=getattr(raw.types, cls_name)(), settings=settings)))
        return {"ok": True}

    # ---- contacts
    async def contacts(self, q: str = "") -> dict:
        res = await self._call(self.client().get_contacts())
        needle = (q or "").strip().lower()
        items = []
        for u in res:
            row = {"id": u.id, "name": full_name(u) or getattr(u, "username", None) or "Unknown", "username": getattr(u, "username", None),
                   "phone": getattr(u, "phone_number", None), "status": enum_name(getattr(u, "status", None)) or None}
            if not needle or needle in row["name"].lower() or needle in (row["username"] or "").lower() or needle in (row["phone"] or ""):
                items.append(row)
        items.sort(key=lambda r: r["name"].lower())
        return {"items": items[:500], "count": len(items)}

    async def add_contact(self, first_name: str, last_name: str = "", phone: str = "", user: str = "") -> dict:
        from pyrogram import raw
        c = self.client()
        first = self._text(first_name, 64)
        last = self._text(last_name or "", 64, allow_empty=True)
        if phone:
            number = parse_phone(phone)
            await self._call(c.invoke(raw.functions.contacts.ImportContacts(contacts=[
                raw.types.InputPhoneContact(client_id=int(time.time()), phone=number, first_name=first, last_name=last)])))
        elif user:
            await self._call(c.add_contact(self._chat_id(user), first, last))
        else:
            raise AdminError(400, "phone number ya username do")
        return {"ok": True}

    async def delete_contact(self, user_id) -> dict:
        await self._call(self.client().delete_contacts([self._chat_id(user_id)]))
        return {"ok": True}
