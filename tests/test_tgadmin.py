"""Dashboard Telegram API (tgadmin.py): lists, filters, messages, profile, privacy, sessions, safety rules."""
import asyncio
import unittest
from types import SimpleNamespace as NS

import tgadmin
from tests.fake_tg import (FakeTg, PrivacyValueAllowAll, PrivacyValueAllowContacts, PrivacyValueAllowUsers, PrivacyValueDisallowAll,
                           chat, dialog, message)
from tgadmin import EVENTS, AdminError, EventBus, TgAdmin


def user(id, deleted=False, contact=False, bot=False, status="RECENTLY"):
    return NS(id=id, is_deleted=deleted, is_contact=contact, is_bot=bot, status=NS(name=status))


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tg = FakeTg()
        self.tg.dialogs = [
            dialog(chat(10, "private", first="Lisa", last="Fan", username="lf"), "hello there", unread=3, pinned=True),
            dialog(chat(11, "bot", first="HelperBot", username="helper_bot"), "start"),
            dialog(chat(-1001, "supergroup", title="Dev Group"), "meeting at 5", unread=1),
            dialog(chat(-1002, "channel", title="News"), "headline"),
            dialog(chat(12, "private", first=None, photo=False), "bye"),                       # deleted account
            dialog(chat(13, "private", first="Bad", last="Guy"), "spam"),                       # blocked
            dialog(chat(1, "private", first="Aman"), "note to self"),                           # saved messages
        ]
        self.tg.users = {10: user(10, contact=True), 11: user(11, bot=True), 12: user(12, deleted=True), 13: user(13), 1: user(1)}
        self.tg.invoke_results["GetBlocked"] = NS(blocked=[NS(peer_id=NS(user_id=13)), NS(peer_id=NS(user_id=77))],
                                                  users=[NS(id=13, first_name="Bad", last_name="Guy", username=None, deleted=False),
                                                         NS(id=77, first_name="Ghost", last_name=None, username="ghost", deleted=False)])
        self.admin = TgAdmin(lambda: self.tg, max_dialogs=100, cache_ttl=60, allow_dangerous=True, min_reload=0)


class TestHelpers(unittest.TestCase):
    def test_phone_parsing(self):
        self.assertEqual(tgadmin.parse_phone("+91 98765-43210"), "+919876543210")
        for bad in ("", "123", "abc", "1" * 20):
            with self.assertRaises(AdminError):
                tgadmin.parse_phone(bad)

    def test_chat_id_parsing_is_strict(self):
        f = TgAdmin._chat_id
        self.assertEqual(f(5), 5)
        self.assertEqual(f("-1001234"), -1001234)
        self.assertEqual(f("me"), "me")
        self.assertEqual(f("@some_user"), "some_user")
        for bad in (True, None, "", "x y", "../etc", 1.5, ["a"], "a"):
            with self.assertRaises(AdminError):
                f(bad)

    def test_privacy_rules_mapping(self):
        p = tgadmin.privacy_from_rules
        self.assertEqual(p([PrivacyValueAllowAll()]), {"value": "everybody", "exceptions": 0})
        self.assertEqual(p([PrivacyValueAllowContacts(), PrivacyValueAllowUsers([1, 2])]), {"value": "contacts", "exceptions": 2})
        self.assertEqual(p([PrivacyValueDisallowAll()]), {"value": "nobody", "exceptions": 0})
        self.assertEqual(p([]), {"value": "nobody", "exceptions": 0})

    def test_message_serialization_kinds(self):
        s = tgadmin.serialize_message
        m = s(message(5, "hello", out=True))
        self.assertEqual((m["id"], m["text"], m["out"], m["kind"], m["from"]), (5, "hello", True, None, "Bob"))
        ph = s(message(6, "", photo=NS(file_size=10, file_name=None, mime_type=None, duration=None, width=1, height=1)))
        self.assertEqual((ph["kind"], ph["size"]), ("photo", 10))
        st = s(message(7, "", sticker=NS(emoji="😀", is_animated=True, file_size=1, file_name=None, mime_type=None, duration=None, width=1, height=1)))
        self.assertEqual((st["kind"], st["emoji"], st["animated"]), ("sticker", "😀", True))
        sv = s(message(8, "", service=Enum_("NEW_CHAT_MEMBERS")))
        self.assertEqual((sv["service"], sv["text"]), (True, "new chat members"))
        fw = s(message(9, "x", forward_from=NS(first_name="Cat", last_name=None, username="c"), reply_to_message_id=4))
        self.assertEqual((fw["fwd"], fw["reply_to"]), ("Cat", 4))
        poll = s(message(10, "", poll=NS(question="Q?", options=[NS(text="a"), NS(text="b")])))
        self.assertEqual(poll["poll"], {"question": "Q?", "options": ["a", "b"]})

    def test_last_preview_labels_media(self):
        p = tgadmin.last_preview
        self.assertEqual(p(message(1, "yo"))["text"], "yo")
        self.assertIn("Photo", p(message(2, "", photo=NS(file_size=1, file_name=None, mime_type=None, duration=None, width=1, height=1)))["text"])
        self.assertEqual(p(None)["text"], "")

    def test_category_rules(self):
        base = dict(type="private", deleted=False, blocked=False)
        ok = tgadmin.category_ok
        self.assertTrue(ok(base, "all") and ok(base, "personal"))
        self.assertFalse(ok(dict(base, blocked=True), "personal"))           # blocked people are not "personal"
        self.assertFalse(ok(dict(base, deleted=True), "personal"))
        self.assertTrue(ok(dict(base, type="bot"), "bots") and not ok(base, "bots"))
        self.assertTrue(ok(dict(base, type="supergroup"), "groups") and ok(dict(base, type="group"), "groups"))
        self.assertTrue(ok(dict(base, type="channel"), "channels"))
        self.assertTrue(ok(dict(base, blocked=True), "blocked") and ok(dict(base, deleted=True), "deleted"))


def Enum_(name):
    from tests.fake_tg import Enum
    return Enum(name)


class TestDialogs(Base):
    async def test_filters_and_counts(self):
        everything = await self.admin.dialogs("all")
        self.assertEqual(everything["total"], 7)
        self.assertEqual(everything["counts"], {"all": 7, "personal": 2, "bots": 1, "groups": 1, "channels": 1, "blocked": 2, "deleted": 1})
        titles = lambda r: [i["title"] for i in r["items"]]
        self.assertEqual(titles(await self.admin.dialogs("personal")), ["Lisa Fan", "Saved Messages"])
        self.assertEqual(titles(await self.admin.dialogs("bots")), ["HelperBot"])
        self.assertEqual(titles(await self.admin.dialogs("groups")), ["Dev Group"])
        self.assertEqual(titles(await self.admin.dialogs("channels")), ["News"])
        self.assertEqual(titles(await self.admin.dialogs("deleted")), ["Deleted Account"])

    async def test_blocked_tab_also_lists_blocked_people_without_a_chat(self):
        r = await self.admin.dialogs("blocked")
        names = sorted(i["title"] for i in r["items"])
        self.assertEqual(names, ["Bad Guy", "Ghost"])                        # Ghost has no dialog but is blocked
        self.assertTrue(all(i["blocked"] for i in r["items"]))

    async def test_search_paging_and_row_fields(self):
        r = await self.admin.dialogs("all", q="dev")
        self.assertEqual([i["title"] for i in r["items"]], ["Dev Group"])
        r = await self.admin.dialogs("all", q="meeting")                       # also matches last message text
        self.assertEqual(r["total"], 1)
        page = await self.admin.dialogs("all", offset=0, limit=3)
        self.assertEqual((len(page["items"]), page["next"], page["total"]), (3, 3, 7))
        last = await self.admin.dialogs("all", offset=6, limit=3)
        self.assertEqual((len(last["items"]), last["next"]), (1, None))
        row = (await self.admin.dialogs("all"))["items"][0]
        self.assertEqual((row["unread"], row["pinned"], row["avatar"], row["contact"]), (3, True, True, True))
        saved = [i for i in (await self.admin.dialogs("all"))["items"] if i["saved"]]
        self.assertEqual(saved[0]["title"], "Saved Messages")

    async def test_list_is_cached_and_invalidated_by_events(self):
        await self.admin.dialogs("all"); await self.admin.dialogs("bots"); await self.admin.dialogs("groups")
        self.assertEqual(self.tg.count("get_dialogs"), 1)                      # one Telegram call for three tabs
        EVENTS.publish({"t": "message", "chat_id": 10})                        # a new message -> next read reloads
        await self.admin.dialogs("all")
        self.assertEqual(self.tg.count("get_dialogs"), 2)

    async def test_a_busy_account_cannot_make_us_reload_every_second(self):
        busy = TgAdmin(lambda: self.tg, cache_ttl=60, min_reload=5.0)
        await busy.dialogs("all")
        for _ in range(20):                                                    # 20 new messages in a row
            EVENTS.publish({"t": "message", "chat_id": 10})
            await busy.dialogs("all")
        self.assertEqual(self.tg.count("get_dialogs"), 1)                      # still the first read
        loaded, items = busy._dialogs
        busy._dialogs = (loaded - 10, items)                                   # 10 seconds later the update is picked up
        await busy.dialogs("all")
        self.assertEqual(self.tg.count("get_dialogs"), 2)
        await busy.dialogs("all")
        self.assertEqual(self.tg.count("get_dialogs"), 2)                      # and not again (no new event)

    async def test_bad_filter_and_disconnected_client(self):
        with self.assertRaises(AdminError) as e:
            await self.admin.dialogs("nope")
        self.assertEqual(e.exception.status, 400)
        self.tg.is_connected = False
        with self.assertRaises(AdminError) as e:
            await TgAdmin(lambda: self.tg).dialogs("all")
        self.assertEqual(e.exception.status, 503)
        with self.assertRaises(AdminError):
            await TgAdmin(lambda: None).me()


class TestMessages(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.tg.history = [message(i, f"m{i}", out=i % 2 == 0, chat_id=10) for i in range(1, 8)]

    async def test_history_is_oldest_first_and_pages_backwards(self):
        r = await self.admin.history(10, 0, 3)
        self.assertEqual([m["id"] for m in r["messages"]], [5, 6, 7])
        self.assertTrue(r["has_more"])
        older = await self.admin.history(10, 5, 3)
        self.assertEqual([m["id"] for m in older["messages"]], [2, 3, 4])
        self.assertEqual(self.tg.calls[-1][2]["offset_id"], 5)

    async def test_send_validates_and_uses_reply(self):
        r = await self.admin.send(10, "hello", reply_to=7)
        self.assertEqual(r["message"]["text"], "hello")
        self.assertEqual(self.tg.calls[-1][2]["reply_to_message_id"], 7)
        for bad in ("", "   ", None, 5, "x" * 4097):
            with self.assertRaises(AdminError):
                await self.admin.send(10, bad)
        with self.assertRaises(AdminError):
            await self.admin.send("../x", "hi")

    async def test_edit_delete_forward_read(self):
        self.assertEqual((await self.admin.edit(10, 7, "new"))["message"]["text"], "new")
        await self.admin.delete_messages(10, [1, 2], revoke=False)
        self.assertEqual(self.tg.calls[-1], ("delete_messages", (10, [1, 2]), {"revoke": False}))
        for bad in ([], ["a"], list(range(101)), "x", [True]):
            with self.assertRaises(AdminError):
                await self.admin.delete_messages(10, bad)
        await self.admin.forward(11, 10, [3])
        self.assertEqual(self.tg.count("forward_messages"), 1)
        await self.admin.mark_read(10)
        self.assertEqual(self.tg.count("read_chat_history"), 1)

    async def test_send_file_photo_or_document_and_size_cap(self):
        await self.admin.send_file(10, b"x" * 10, "pic.jpg", "cap", as_photo=True)
        self.assertEqual(self.tg.calls[-1][0], "send_photo")
        await self.admin.send_file(10, b"x" * 10, "../../evil name.pdf", "", as_photo=False)
        name, args, kw = self.tg.calls[-1]
        self.assertEqual(name, "send_document")
        self.assertNotIn("/", args[1])                                           # path tricks are flattened
        with self.assertRaises(AdminError) as e:
            await self.admin.send_file(10, b"x" * (tgadmin.MEDIA_MAX + 1), "big.bin")
        self.assertEqual(e.exception.status, 413)
        with self.assertRaises(AdminError):
            await self.admin.send_file(10, b"", "empty.txt")

    async def test_search_in_chat(self):
        r = await self.admin.search(10, "m3")
        self.assertEqual([m["id"] for m in r["messages"]], [3])

    async def test_media_download_rules(self):
        photo = NS(file_size=5, file_name=None, mime_type=None)
        self.tg.messages[5] = message(5, "", photo=photo)
        data, mime, name = await self.admin.media(10, 5)
        self.assertEqual((data, mime), (b"BYTES", "image/jpeg"))
        huge = NS(file_size=tgadmin.MEDIA_MAX + 1, file_name="a.zip", mime_type="application/zip")
        self.tg.messages[6] = message(6, "", document=huge)
        with self.assertRaises(AdminError) as e:
            await self.admin.media(10, 6)
        self.assertEqual(e.exception.status, 413)
        self.tg.messages[7] = message(7, "just text")
        with self.assertRaises(AdminError) as e:
            await self.admin.media(10, 7)
        self.assertEqual(e.exception.status, 404)
        video = NS(file_size=5, file_name="v.mp4", mime_type="video/mp4", thumbs=[NS(file_id="t1", file_size=3)])
        self.tg.messages[8] = message(8, "", video=video)
        await self.admin.media(10, 8, thumb=True)
        self.assertEqual(self.tg.calls[-1], ("download_media", ("t1",), {}))

    async def test_avatar_is_cached(self):
        await self.admin.dialogs("all")                                         # remembers the photo ids
        self.assertEqual(await self.admin.avatar(10), b"BYTES")
        await self.admin.avatar(10)
        self.assertEqual(self.tg.count("download_media"), 1)
        self.assertIsNone(await self.admin.avatar(12))                           # no photo -> None, no crash


class TestProfile(Base):
    async def test_me_profile_and_username(self):
        me = await self.admin.me()
        self.assertEqual((me["first_name"], me["bio"], me["phone"]), ("Aman", "my bio", "919876543210"))
        await self.admin.update_profile("  New  ", " Name ", "about me")
        self.assertEqual(self.tg.calls[[c[0] for c in self.tg.calls].index("update_profile")][2],
                         {"first_name": "New", "last_name": "Name", "bio": "about me"})
        for args in (("", "", ""), ("x" * 65, "", ""), ("A", "", "b" * 141)):
            with self.assertRaises(AdminError):
                await self.admin.update_profile(*args)
        await self.admin.set_username("@new_name1")
        self.assertIn(("set_username", ("new_name1",), {}), self.tg.calls)
        await self.admin.set_username("")                                        # remove username
        self.assertIn(("set_username", (None,), {}), self.tg.calls)
        for bad in ("ab", "1abcde", "has space", "x" * 40):
            with self.assertRaises(AdminError):
                await self.admin.set_username(bad)

    async def test_username_check(self):
        self.tg.invoke_results["CheckUsername"] = True
        self.assertEqual(await self.admin.check_username("hello_world"), {"available": True})
        self.assertFalse((await self.admin.check_username("ab"))["available"])     # invalid -> no Telegram call
        self.assertEqual(self.tg.count("invoke:CheckUsername"), 1)
        self.assertTrue((await self.admin.check_username(""))["empty"])

    async def test_phone_change_needs_the_code_flow(self):
        self.tg.invoke_results["SendChangePhoneCode"] = NS(phone_code_hash="h1", timeout=60)
        with self.assertRaises(AdminError):                                      # confirming first is refused
            await self.admin.phone_confirm("+919000000000", "12345")
        r = await self.admin.phone_request("+91 90000-00000")
        self.assertEqual(r["phone"], "+919000000000")
        with self.assertRaises(AdminError):                                      # different number than the one the code went to
            await self.admin.phone_confirm("+919111111111", "12345")
        with self.assertRaises(AdminError):                                      # nonsense code
            await self.admin.phone_confirm("+919000000000", "1")
        await self.admin.phone_confirm("+919000000000", "12-345")
        sent = [c for c in self.tg.calls if c[0] == "invoke:ChangePhone"][0][2]
        self.assertEqual((sent["phone_number"], sent["phone_code_hash"], sent["phone_code"]), ("+919000000000", "h1", "12345"))
        with self.assertRaises(AdminError):                                      # the code is single use
            await self.admin.phone_confirm("+919000000000", "12345")

    async def test_phone_code_expires(self):
        self.tg.invoke_results["SendChangePhoneCode"] = NS(phone_code_hash="h1")
        await self.admin.phone_request("+919000000000")
        self.admin._phone_pending["ts"] -= 700
        with self.assertRaises(AdminError):
            await self.admin.phone_confirm("+919000000000", "12345")

    async def test_photo_set_and_delete(self):
        await self.admin.set_photo(b"img", "me.png")
        self.assertEqual(self.tg.count("set_profile_photo"), 1)
        with self.assertRaises(AdminError):
            await self.admin.set_photo(b"", "x.png")
        with self.assertRaises(AdminError) as e:
            await self.admin.delete_photo()                                      # no photo yet
        self.assertEqual(e.exception.status, 404)
        self.tg.invoke_results["photos"] = [NS(file_id="pid")]
        await self.admin.delete_photo()
        self.assertEqual(self.tg.calls[-2 if self.tg.calls[-1][0] == "get_chat" else -1][0] in ("delete_profile_photos", "get_chat"), True)
        self.assertEqual(self.tg.count("delete_profile_photos"), 1)


class TestSettings(Base):
    async def test_privacy_list_and_set(self):
        self.tg.invoke_results["GetPrivacy"] = NS(rules=[PrivacyValueAllowContacts()])
        res = await self.admin.privacy_get()
        self.assertEqual(len(res["items"]), len(tgadmin.PRIVACY_KEYS))
        self.assertEqual(res["items"][0]["value"], "contacts")
        await self.admin.privacy_set("last_seen", "nobody")
        sent = [c for c in self.tg.calls if c[0] == "invoke:SetPrivacy"][0][2]
        self.assertEqual(type(sent["rules"][0]).__name__, "InputPrivacyValueDisallowAll")
        with self.assertRaises(AdminError):
            await self.admin.privacy_set("last_seen", "everyone")
        with self.assertRaises(AdminError):
            await self.admin.privacy_set("nonsense", "nobody")
        with self.assertRaises(AdminError):                                      # Telegram does not allow this combination
            await self.admin.privacy_set("find_by_phone", "nobody")

    async def test_privacy_change_with_exceptions_needs_force(self):
        self.tg.invoke_results["GetPrivacy"] = NS(rules=[PrivacyValueAllowContacts(), PrivacyValueAllowUsers([1, 2, 3])])
        with self.assertRaises(AdminError) as e:
            await self.admin.privacy_set("phone", "nobody")
        self.assertEqual(e.exception.status, 409)
        self.assertEqual(self.tg.count("invoke:SetPrivacy"), 0)                  # nothing was changed
        await self.admin.privacy_set("phone", "nobody", force=True)
        self.assertEqual(self.tg.count("invoke:SetPrivacy"), 1)

    async def test_current_session_can_never_be_terminated(self):
        self.tg.invoke_results["GetAuthorizations"] = NS(authorizations=[
            NS(hash=0, current=True, device_model="Bot", platform="x", system_version="", app_name="Pyro", app_version="2", ip="1.1.1.1",
               country="IN", region="", date_created=0, date_active=1760000000, official_app=False),
            NS(hash=555, current=False, device_model="Phone", platform="ios", system_version="17", app_name="Telegram", app_version="10", ip="2.2.2.2",
               country="US", region="", date_created=0, date_active=1760000100, official_app=True)])
        s = await self.admin.sessions()
        self.assertEqual([x["current"] for x in s["items"]], [True, False])
        for h in (0, "0"):
            with self.assertRaises(AdminError):
                await self.admin.terminate_session(h, confirm=True)
        with self.assertRaises(AdminError):                                      # needs explicit confirm
            await self.admin.terminate_session(555, confirm=False)
        await self.admin.terminate_session("555", confirm=True)
        done = await self.admin.terminate_other_sessions(confirm=True)
        self.assertEqual(done["terminated"], 1)
        hashes = [c[2]["hash"] for c in self.tg.calls if c[0] == "invoke:ResetAuthorization"]
        self.assertEqual(hashes, [555, 555])                                     # only the other device, never hash 0

    async def test_ttl_notify_history_ttl_validation(self):
        await self.admin.ttl_set(90)
        for bad in (0, 31, -1):
            with self.assertRaises(AdminError):
                await self.admin.ttl_set(bad)
        await self.admin.history_ttl_set(86400)
        with self.assertRaises(AdminError):
            await self.admin.history_ttl_set(5)
        await self.admin.notify_set("groups", True, False)
        sent = [c for c in self.tg.calls if c[0] == "invoke:UpdateNotifySettings"][0][2]
        self.assertEqual((sent["settings"].mute_until, sent["settings"].show_previews), (tgadmin.MUTE_FOREVER, False))
        with self.assertRaises(AdminError):
            await self.admin.notify_set("everything", True, True)

    async def test_contacts_search_add_delete(self):
        r = await self.admin.contacts()
        self.assertEqual([c["name"] for c in r["items"]], ["Adam B", "Zoya"])
        self.assertEqual([c["name"] for c in (await self.admin.contacts("zoy"))["items"]], ["Zoya"])
        await self.admin.add_contact("New", "", user="@someone")
        self.assertEqual(self.tg.calls[-1], ("add_contact", ("someone", "New", ""), {}))
        await self.admin.add_contact("Imp", "", phone="+91 99999 11111")
        self.assertEqual(self.tg.count("invoke:ImportContacts"), 1)
        with self.assertRaises(AdminError):
            await self.admin.add_contact("X")                                    # neither phone nor username
        await self.admin.delete_contact(20)
        self.assertEqual(self.tg.calls[-1], ("delete_contacts", ([20],), {}))

    async def test_block_and_unblock_refresh_the_list(self):
        await self.admin.dialogs("all")
        await self.admin.block(10)
        self.assertEqual(self.tg.count("block_user"), 1)
        await self.admin.dialogs("all")
        self.assertEqual(self.tg.count("get_dialogs"), 2)                        # cache dropped so the tab updates


class TestChatActions(Base):
    async def test_safe_actions(self):
        for action in ("pin", "unpin", "mute", "unmute", "mark_unread", "archive", "unarchive", "block", "unblock"):
            await self.admin.chat_action(10, action)
        names = [c[0] for c in self.tg.calls]
        self.assertIn("invoke:ToggleDialogPin", names)
        self.assertIn("invoke:UpdateNotifySettings", names)
        self.assertIn("invoke:MarkDialogUnread", names)
        self.assertIn("archive_chats", names)
        mute = [c for c in self.tg.calls if c[0] == "invoke:UpdateNotifySettings"]
        self.assertEqual([m[2]["settings"].mute_until for m in mute], [tgadmin.MUTE_FOREVER, 0])
        with self.assertRaises(AdminError):
            await self.admin.chat_action(10, "format_disk")

    async def test_dangerous_actions_need_confirm_and_the_switch(self):
        for action in ("clear", "delete", "leave"):
            with self.assertRaises(AdminError):
                await self.admin.chat_action(10, action)                          # no confirm
            with self.assertRaises(AdminError):
                await self.admin.chat_action(10, action, confirm="yes")           # must be literally True
        self.assertEqual(self.tg.count("invoke:DeleteHistory"), 0)
        await self.admin.chat_action(10, "clear", confirm=True)
        await self.admin.chat_action(-1001, "leave", confirm=True)
        self.assertEqual(self.tg.count("invoke:DeleteHistory"), 1)
        self.assertEqual(self.tg.count("leave_chat"), 1)
        locked = TgAdmin(lambda: self.tg, allow_dangerous=False)
        with self.assertRaises(AdminError) as e:
            await locked.chat_action(10, "delete", confirm=True)
        self.assertEqual(e.exception.status, 403)
        with self.assertRaises(AdminError):
            await locked.terminate_other_sessions(confirm=True)


class TestErrors(Base):
    async def test_flood_wait_and_telegram_errors_are_translated(self):
        class FloodWait(Exception):
            value = 30
        class RpcLike(Exception):
            ID = "USERNAME_OCCUPIED"
        self.tg.fail_next = FloodWait()
        with self.assertRaises(AdminError) as e:
            await self.admin.send(10, "x")
        self.assertEqual(e.exception.status, 429)
        self.assertIn("30", e.exception.message)
        self.tg.fail_next = RpcLike()
        with self.assertRaises(AdminError) as e:
            await self.admin.set_username("taken_name")
        self.assertEqual((e.exception.status, e.exception.message), (400, "Telegram: USERNAME_OCCUPIED"))
        self.tg.fail_next = RuntimeError("secret token sk-abcdef inside")
        with self.assertRaises(AdminError) as e:
            await self.admin.send(10, "x")
        self.assertEqual(e.exception.status, 500)
        self.assertNotIn("secret", e.exception.message)                           # raw error text never reaches the browser


class TestEventBus(unittest.IsolatedAsyncioTestCase):
    async def test_publish_subscribe_and_slow_consumers(self):
        bus = EventBus()
        q = bus.subscribe()
        seen = []
        bus.add_listener(seen.append)
        bus.publish({"t": "message", "chat_id": 1})
        self.assertEqual(await asyncio.wait_for(q.get(), 1), {"t": "message", "chat_id": 1})
        self.assertEqual(len(seen), 1)
        for i in range(500):                                                      # a stuck browser must not grow memory or block
            bus.publish({"i": i})
        self.assertLessEqual(q.qsize(), 100)
        bus.add_listener(lambda e: 1 / 0)                                         # a broken listener never breaks publishing
        bus.publish({"t": "x"})
        bus.unsubscribe(q)
        self.assertEqual(bus.subscribers, 0)


if __name__ == "__main__":
    unittest.main()
