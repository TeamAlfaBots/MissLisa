"""In-memory stand-in for a connected Pyrogram client, for the dashboard's Telegram API (tgadmin.py)."""
import io
from datetime import datetime, timezone
from types import SimpleNamespace as NS


def dt(hour=8, day=24):
    return datetime(2026, 10, day, hour, 0, tzinfo=timezone.utc)


class Enum:
    def __init__(self, name): self.name = name


def chat(id, kind="private", title=None, first=None, last=None, username=None, photo=True):
    return NS(id=id, type=Enum(kind.upper()), title=title, first_name=first, last_name=last, username=username,
              photo=NS(small_file_id=f"photo{id}") if photo else None, is_verified=False, bio=None, description=None, members_count=None)


def message(id, text="hi", out=False, chat_id=100, sender="Bob", **kw):
    base = dict(id=id, text=text, caption=None, date=dt(8 + id % 10), outgoing=out, chat=NS(id=chat_id), service=None,
                from_user=NS(id=7, first_name=sender, last_name=None, username="bob"), sender_chat=None, reply_to_message_id=None,
                edit_date=None, views=None, forward_from=None, forward_from_chat=None, forward_sender_name=None, empty=False)
    for attr in ("photo", "video", "audio", "voice", "document", "sticker", "animation", "video_note", "contact", "location",
                 "venue", "poll", "dice", "game"):
        base[attr] = None
    base.update(kw)
    return NS(**base)


def dialog(c, text="hello", unread=0, pinned=False, out=False):
    return NS(chat=c, top_message=message(1, text, out=out, chat_id=c.id), unread_messages_count=unread, unread_mentions_count=0,
              unread_mark=False, is_pinned=pinned)


# raw privacy rule classes (privacy_from_rules looks at the class name)
class PrivacyValueAllowAll: pass
class PrivacyValueAllowContacts: pass
class PrivacyValueDisallowAll: pass
class PrivacyValueAllowUsers:
    def __init__(self, users): self.users = users
class PrivacyValueDisallowUsers:
    def __init__(self, users): self.users = users


class FakeTg:
    """Records every call in self.calls as (name, args, kwargs)."""

    def __init__(self):
        self.is_connected = True
        self.me = NS(id=1, first_name="Aman", username="aman")
        self.calls = []
        self.dialogs = []
        self.users = {}
        self.history = []
        self.invoke_results = {}
        self.fail_next = None
        self.blocked_pages = []
        self.messages = {}
        self.media = {}

    def _rec(self, name, *a, **k):
        self.calls.append((name, a, k))
        if self.fail_next:
            exc, self.fail_next = self.fail_next, None
            raise exc

    def count(self, name): return sum(1 for c in self.calls if c[0] == name)

    async def get_me(self):
        return NS(id=1, first_name="Aman", last_name="K", username="aman", phone_number="919876543210", is_premium=False, photo=NS())

    async def get_chat(self, cid):
        self._rec("get_chat", cid)
        if cid == "me":
            return NS(id=1, bio="my bio")
        for d in self.dialogs:
            if d.chat.id == cid:
                return d.chat
        raise KeyError(cid)

    async def get_dialogs(self, limit=0):
        self._rec("get_dialogs", limit=limit)
        for d in self.dialogs[: limit or None]:
            yield d

    async def get_users(self, ids):
        self._rec("get_users", list(ids))
        return [self.users[i] for i in ids if i in self.users]

    async def get_chat_history(self, cid, limit=0, offset_id=0):
        self._rec("get_chat_history", cid, limit=limit, offset_id=offset_id)
        items = [m for m in sorted(self.history, key=lambda m: -m.id) if not offset_id or m.id < offset_id]
        for m in items[:limit]:
            yield m

    async def search_messages(self, cid, query="", limit=0):
        self._rec("search_messages", cid, query=query)
        for m in self.history:
            if query.lower() in (m.text or "").lower():
                yield m

    async def send_message(self, chat_id, text, reply_to_message_id=None):
        self._rec("send_message", chat_id, text, reply_to_message_id=reply_to_message_id)
        return message(99, text, out=True, chat_id=chat_id)

    async def send_photo(self, chat_id, photo, caption="", reply_to_message_id=None):
        self._rec("send_photo", chat_id, photo.name, caption=caption)
        return message(98, caption, out=True, chat_id=chat_id)

    async def send_document(self, chat_id, document, caption="", file_name=None, reply_to_message_id=None):
        self._rec("send_document", chat_id, document.name, caption=caption, file_name=file_name)
        return message(97, caption, out=True, chat_id=chat_id)

    async def edit_message_text(self, chat_id, message_id, text):
        self._rec("edit_message_text", chat_id, message_id, text)
        return message(message_id, text, out=True, chat_id=chat_id)

    async def delete_messages(self, chat_id, ids, revoke=True): self._rec("delete_messages", chat_id, ids, revoke=revoke)
    async def forward_messages(self, chat_id, from_chat_id, message_ids): self._rec("forward_messages", chat_id, from_chat_id, message_ids)
    async def read_chat_history(self, cid): self._rec("read_chat_history", cid)
    async def block_user(self, uid): self._rec("block_user", uid)
    async def unblock_user(self, uid): self._rec("unblock_user", uid)
    async def archive_chats(self, cid): self._rec("archive_chats", cid)
    async def unarchive_chats(self, cid): self._rec("unarchive_chats", cid)
    async def leave_chat(self, cid, delete=False): self._rec("leave_chat", cid, delete=delete)
    async def update_profile(self, **kw): self._rec("update_profile", **kw)
    async def set_username(self, name): self._rec("set_username", name)
    async def set_profile_photo(self, photo): self._rec("set_profile_photo", photo.name)
    async def delete_profile_photos(self, ids): self._rec("delete_profile_photos", ids)
    async def delete_contacts(self, ids): self._rec("delete_contacts", ids)
    async def add_contact(self, uid, first, last=""): self._rec("add_contact", uid, first, last)

    async def get_chat_photos(self, cid, limit=0):
        self._rec("get_chat_photos", cid)
        for p in self.invoke_results.get("photos", []):
            yield p

    async def get_contacts(self):
        return [NS(id=20, first_name="Zoya", last_name=None, username="zoya", phone_number="91111", status=Enum("ONLINE")),
                NS(id=21, first_name="Adam", last_name="B", username=None, phone_number=None, status=None)]

    async def resolve_peer(self, cid):
        self._rec("resolve_peer", cid)
        return NS(peer_id=cid)

    async def get_messages(self, cid, mid):
        self._rec("get_messages", cid, mid)
        return self.messages.get(mid) or NS(empty=True)

    async def download_media(self, target, in_memory=False):
        self._rec("download_media", target)
        data = self.media.get(target if isinstance(target, str) else getattr(target, "id", None), b"BYTES")
        bio = io.BytesIO(data)
        bio.name = "x.bin"
        return bio

    async def invoke(self, req):
        name = type(req).__name__
        self._rec("invoke:" + name, **req.__dict__)
        res = self.invoke_results.get(name)
        if callable(res):
            res = res(req)
        return res if res is not None else NS(ok=True)
