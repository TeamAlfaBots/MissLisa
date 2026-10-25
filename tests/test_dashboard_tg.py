"""Dashboard web API for the Telegram account: auth, CSRF, wiring of every operation, media safety, JS <-> server contract."""
import io
import json
import os
import re
import unittest
from types import SimpleNamespace as NS

import dashboard
from dashboard import COOKIE, Dashboard
from memory import Memory
from tests.fake_tg import FakeTg, PrivacyValueAllowContacts, chat, dialog, message
from tests.fakes import FakeDB
from tgadmin import TgAdmin

PASSWORD = "test-admin-pass-123"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeControl:
    def __init__(self, tg):
        self.tg, self.glob, self.chats = tg, True, {}
    def client(self): return self.tg
    async def ai_chat_get(self, cid): return self.chats.get(cid, True)
    async def ai_chat_set(self, cid, v): self.chats[cid] = v; return True
    async def ai_global_get(self): return self.glob
    async def ai_global_set(self, v): self.glob = v; return True
    def info(self): return {"mode": "userbot", "persona": "Lisa", "lisa": {"enabled": True, "loaded": True, "examples": 5, "per_reply": 3}, "providers": []}


def rq(dash=None, *, cookie=None, query=None, body=None, op="", kind="", headers=None, csrf=None, form=None, raw_json=None):
    h = dict(headers or {})
    if csrf:
        h["X-CSRF-Token"] = csrf
    async def js():
        if raw_json is not None:
            raise ValueError("bad")
        return body
    async def post(): return form or {}
    return NS(cookies={COOKIE: cookie} if cookie else {}, query=query or {}, headers=h, match_info={"op": op, "kind": kind},
              json=js, post=post, remote="1.1.1.1", secure=False)


class Case(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tg = FakeTg()
        self.tg.dialogs = [dialog(chat(10, "private", first="Lisa"), "hello", unread=2), dialog(chat(11, "bot", first="Bot", username="b_bot"), "x")]
        self.tg.users = {10: NS(id=10, is_deleted=False, is_contact=True, is_bot=False, status=NS(name="ONLINE")),
                         11: NS(id=11, is_deleted=False, is_contact=False, is_bot=True, status=None)}
        self.tg.invoke_results["GetBlocked"] = NS(blocked=[], users=[])
        self.tg.history = [message(i, f"m{i}", chat_id=10) for i in range(1, 6)]
        self.ctl = FakeControl(self.tg)
        self.dash = Dashboard(lambda: Memory(FakeDB()), password=PASSWORD, control=self.ctl, admin=TgAdmin(lambda: self.tg, cache_ttl=0, min_reload=0))
        self.cookie = self.dash.make_cookie()
        self.csrf = self.dash.csrf_token(rq(cookie=self.cookie))

    def get(self, op, **q): return self.dash.api_tg_get(rq(cookie=self.cookie, op=op, query=q))
    def post(self, op, body=None, csrf="auto", cookie="auto"):
        cookie = self.cookie if cookie == "auto" else cookie
        csrf = self.csrf if csrf == "auto" else csrf
        return self.dash.api_tg_post(rq(cookie=cookie, op=op, body=body or {}, csrf=csrf))


class TestAuthAndCsrf(Case):
    async def test_every_read_and_write_needs_login(self):
        for op in self.dash._get_ops():
            r = await self.dash.api_tg_get(rq(op=op))
            self.assertEqual(r.status, 401, op)
        for op in self.dash._post_ops():
            r = await self.dash.api_tg_post(rq(op=op, body={}, csrf="x"))
            self.assertEqual(r.status, 401, op)
        for handler in (self.dash.api_media, self.dash.api_avatar, self.dash.api_events, self.dash.api_upload, self.dash.static):
            self.assertEqual((await handler(rq(kind="photo", query={"id": "1"}))).status, 401)
        self.assertEqual((await self.dash.api_tg_get(rq(cookie="123.deadbeef", op="me"))).status, 401)

    async def test_writes_need_the_csrf_token(self):
        for token in (None, "", "wrong", self.csrf[:-1], self.csrf.upper()):
            r = await self.post("read", {"chat_id": 10}, csrf=token)
            self.assertEqual(r.status, 403, token)
        self.assertEqual(self.tg.count("read_chat_history"), 0)                  # nothing happened
        self.assertEqual((await self.post("read", {"chat_id": 10})).status, 200)
        self.assertEqual(self.tg.count("read_chat_history"), 1)

    async def test_csrf_token_is_bound_to_the_session(self):
        other_cookie = self.dash.make_cookie(now=__import__("time").time() + 5)
        self.assertNotEqual(self.csrf, self.dash.csrf_token(rq(cookie=other_cookie)))
        r = await self.post("read", {"chat_id": 10}, cookie=other_cookie)         # token of another session
        self.assertEqual(r.status, 403)
        other_dash = Dashboard(lambda: None, password="another-password-1", control=self.ctl, admin=TgAdmin(lambda: self.tg))
        self.assertNotEqual(self.csrf, other_dash.csrf_token(rq(cookie=self.cookie)))

    async def test_cross_site_origin_is_refused_even_with_a_valid_token(self):
        good = rq(cookie=self.cookie, op="read", body={"chat_id": 10}, csrf=self.csrf, headers={"Origin": "https://evil.example", "Host": "bot.onrender.com"})
        self.assertEqual((await self.dash.api_tg_post(good)).status, 403)
        same = rq(cookie=self.cookie, op="read", body={"chat_id": 10}, csrf=self.csrf, headers={"Origin": "https://bot.onrender.com", "Host": "bot.onrender.com"})
        self.assertEqual((await self.dash.api_tg_post(same)).status, 200)

    async def test_page_embeds_the_token_and_login_page_does_not(self):
        page = await self.dash.page(rq(cookie=self.cookie))
        self.assertIn(f'content="{self.csrf}"', page.text)
        self.assertNotIn("__CSRF__", page.text)
        login = await self.dash.page(rq())
        self.assertNotIn("csrf", login.text.lower())

    async def test_bad_bodies_and_unknown_ops(self):
        self.assertEqual((await self.dash.api_tg_post(rq(cookie=self.cookie, op="read", csrf=self.csrf, raw_json=1))).status, 400)
        self.assertEqual((await self.post("read", body=None)).status, 400)        # missing chat id
        self.assertEqual((await self.dash.api_tg_post(rq(cookie=self.cookie, op="read", csrf=self.csrf, body=["x"]))).status, 400)
        self.assertEqual((await self.post("rm_rf")).status, 404)
        self.assertEqual((await self.get("rm_rf")).status, 404)
        r = await self.get("history", chat_id="10", limit="abc")
        self.assertEqual(r.status, 400)

    async def test_without_a_connected_account_every_operation_reports_503_not_a_crash(self):
        self.tg.is_connected = False
        self.assertEqual((await self.get("dialogs")).status, 503)
        self.assertEqual((await self.post("send", {"chat_id": 10, "text": "x"})).status, 503)
        bare = Dashboard(lambda: None, password=PASSWORD)                         # no control at all
        ck = bare.make_cookie()
        self.assertEqual((await bare.api_tg_get(rq(cookie=ck, op="me"))).status, 503)
        self.assertEqual((await bare.api_tg_get(rq(cookie=ck, op="ai"))).status, 503)


class TestOperations(Case):
    async def test_chat_list_and_filters(self):
        r = await self.get("dialogs", filter="bots")
        self.assertEqual([i["title"] for i in r.data["items"]], ["Bot"])
        self.assertEqual(r.data["counts"]["all"], 2)
        r = await self.get("dialogs", filter="all", q="lis", limit="1", offset="0")
        self.assertEqual(r.data["total"], 1)
        self.assertEqual((await self.get("dialogs", filter="evil")).status, 400)

    async def test_history_chat_info_search(self):
        r = await self.get("history", chat_id="10", limit="2", before="0")
        self.assertEqual([m["id"] for m in r.data["messages"]], [4, 5])
        self.assertEqual((await self.get("search", chat_id="10", q="m2")).data["messages"][0]["id"], 2)
        info = await self.get("chat", chat_id="10")
        self.assertEqual((info.data["id"], info.data["type"]), (10, "private"))

    async def test_send_edit_delete_forward(self):
        r = await self.post("send", {"chat_id": 10, "text": "hey", "reply_to": 3})
        self.assertEqual(r.data["message"]["text"], "hey")
        self.assertEqual(self.tg.calls[-1][2]["reply_to_message_id"], 3)
        self.assertEqual((await self.post("edit", {"chat_id": 10, "message_id": 5, "text": "fixed"})).status, 200)
        r = await self.post("delete", {"chat_id": 10, "ids": [1], "revoke": False})
        self.assertEqual(self.tg.calls[-1][2], {"revoke": False})
        r = await self.post("delete", {"chat_id": 10, "ids": [1]})
        self.assertEqual(self.tg.calls[-1][2], {"revoke": True})
        self.assertEqual((await self.post("delete", {"chat_id": 10, "ids": "1"})).status, 400)
        self.assertEqual((await self.post("forward", {"to_chat": 11, "from_chat": 10, "ids": [2]})).status, 200)
        self.assertEqual((await self.post("send", {"chat_id": 10, "text": ""})).status, 400)

    async def test_dangerous_actions_need_a_real_true_not_a_string(self):
        for confirm in (None, False, "true", 1, "yes"):
            r = await self.post("chat_action", {"chat_id": 10, "action": "delete", "confirm": confirm})
            self.assertEqual(r.status, 400, confirm)
        self.assertEqual(self.tg.count("invoke:DeleteHistory"), 0)
        r = await self.post("chat_action", {"chat_id": 10, "action": "clear", "confirm": True})
        self.assertEqual(r.status, 200)
        for op, body in (("session_end", {"hash": "5"}), ("sessions_end_others", {})):
            self.assertEqual((await self.post(op, body)).status, 400, op)         # confirm missing

    async def test_profile_and_username(self):
        r = await self.post("profile", {"first_name": "Neha", "last_name": "S", "bio": "hi"})
        self.assertEqual(r.status, 200)
        self.assertEqual((await self.post("profile", {"first_name": "", "bio": ""})).status, 400)
        self.assertEqual((await self.post("username", {"username": "@neha_s1"})).status, 200)
        self.assertEqual((await self.post("username", {"username": "no"})).status, 400)
        self.tg.invoke_results["CheckUsername"] = False
        self.assertFalse((await self.get("username_check", username="takenname")).data["available"])

    async def test_privacy_blocked_sessions_ttl_notify_contacts(self):
        self.tg.invoke_results["GetPrivacy"] = NS(rules=[PrivacyValueAllowContacts()])
        self.assertEqual((await self.get("privacy")).data["items"][0]["value"], "contacts")
        self.assertEqual((await self.post("privacy", {"key": "phone", "value": "nobody"})).status, 200)
        self.assertEqual((await self.post("privacy", {"key": "phone", "value": "everybody!"})).status, 400)
        self.assertEqual((await self.get("blocked")).data["count"], 0)
        self.assertEqual((await self.post("block", {"user_id": "@spammer"})).status, 200)
        self.assertEqual((await self.post("unblock", {"user_id": 5})).status, 200)
        self.assertEqual((await self.post("ttl", {"days": 365})).status, 200)
        self.assertEqual((await self.post("ttl", {"days": 1})).status, 400)
        self.assertEqual((await self.post("history_ttl", {"period": 604800})).status, 200)
        self.assertEqual((await self.post("notify", {"scope": "groups", "muted": True, "previews": False})).status, 200)
        self.assertEqual((await self.get("contacts")).data["count"], 2)
        self.assertEqual((await self.post("contact_add", {"first_name": "Z", "user": "@zedzed"})).status, 200)
        self.assertEqual((await self.post("contact_delete", {"user_id": 20})).status, 200)

    async def test_ai_switches_per_chat_and_global(self):
        r = await self.get("ai", chat_id="10")
        self.assertEqual((r.data["global"], r.data["chat"], r.data["info"]["persona"]), (True, True, "Lisa"))
        await self.post("ai_chat", {"chat_id": 10, "enabled": False})
        self.assertFalse(self.ctl.chats[10])
        await self.post("ai_global", {"enabled": False})
        self.assertFalse(self.ctl.glob)
        r = await self.post("ai_global", {"enabled": "false"})                    # a string is not "true"
        self.assertFalse(self.ctl.glob)


class TestBinaryEndpoints(Case):
    def media_req(self, **q): return rq(cookie=self.cookie, query=q)

    async def test_image_is_inline_everything_else_is_a_neutral_download(self):
        self.tg.messages[5] = message(5, "", photo=NS(file_size=5, file_name=None, mime_type=None))
        r = await self.dash.api_media(self.media_req(chat_id="10", id="5"))
        self.assertEqual((r.status, r.content_type, r.body), (200, "image/jpeg", b"BYTES"))
        self.assertTrue(r.headers["Content-Disposition"].startswith("inline"))
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("script-src 'self'", r.headers["Content-Security-Policy"])
        for evil_mime, evil_name in (("text/html", "x.html"), ("image/svg+xml", "x.svg"), ("application/pdf", "x.pdf"), ("application/x-msdownload", "a.exe")):
            self.tg.messages[6] = message(6, "", document=NS(file_size=5, file_name=evil_name, mime_type=evil_mime))
            r = await self.dash.api_media(self.media_req(chat_id="10", id="6"))
            self.assertEqual(r.content_type, "application/octet-stream", evil_mime)
            self.assertTrue(r.headers["Content-Disposition"].startswith("attachment"), evil_mime)
        r = await self.dash.api_media(self.media_req(chat_id="10", id="5", dl="1"))
        self.assertTrue(r.headers["Content-Disposition"].startswith("attachment"))

    async def test_filename_cannot_break_the_header(self):
        self.tg.messages[7] = message(7, "", document=NS(file_size=5, file_name='a"\r\nSet-Cookie: x=1.txt', mime_type="text/plain"))
        r = await self.dash.api_media(self.media_req(chat_id="10", id="7"))
        disp = r.headers["Content-Disposition"]
        self.assertNotIn("\r", disp)
        self.assertNotIn("\n", disp)
        self.assertEqual(disp.count('"'), 2)

    async def test_media_errors(self):
        self.assertEqual((await self.dash.api_media(self.media_req(chat_id="10"))).status, 400)            # no id
        self.assertEqual((await self.dash.api_media(self.media_req(chat_id="10", id="abc"))).status, 400)
        self.assertEqual((await self.dash.api_media(self.media_req(chat_id="x y", id="1"))).status, 400)
        self.assertEqual((await self.dash.api_media(self.media_req(chat_id="10", id="999"))).status, 404)

    async def test_avatar(self):
        await self.get("dialogs")
        r = await self.dash.api_avatar(self.media_req(id="10"))
        self.assertEqual((r.status, r.content_type), (200, "image/jpeg"))
        self.assertEqual((await self.dash.api_avatar(self.media_req(id="bad id"))).status, 400)

    async def test_uploads(self):
        photo = NS(file=io.BytesIO(b"imgdata"), filename="me.png")
        r = await self.dash.api_upload(rq(cookie=self.cookie, kind="photo", csrf=self.csrf, form={"file": photo}))
        self.assertEqual(r.status, 200)
        self.assertEqual(self.tg.count("set_profile_photo"), 1)
        doc = NS(file=io.BytesIO(b"abc"), filename="a.pdf")
        r = await self.dash.api_upload(rq(cookie=self.cookie, kind="file", csrf=self.csrf,
                                          form={"file": doc, "chat_id": "10", "caption": "here", "as_photo": "0"}))
        self.assertEqual(r.status, 200)
        self.assertEqual(self.tg.count("send_document"), 1)
        no_token = await self.dash.api_upload(rq(cookie=self.cookie, kind="file", form={"file": doc, "chat_id": "10"}))
        self.assertEqual(no_token.status, 403)
        self.assertEqual((await self.dash.api_upload(rq(cookie=self.cookie, kind="evil", csrf=self.csrf, form={"file": doc}))).status, 404)
        self.assertEqual((await self.dash.api_upload(rq(cookie=self.cookie, kind="file", csrf=self.csrf, form={}))).status, 400)


class TestStaticAndContract(Case):
    async def test_static_files_login_required_and_whitelisted(self):
        for name, ctype in (("app.js", "javascript"), ("app.css", "css")):
            r = await self.dash.static(rq(cookie=self.cookie, op="", kind="") if False else NS(cookies={COOKIE: self.cookie}, match_info={"name": name}))
            self.assertEqual(r.status, 200)
            self.assertIn(ctype, r.headers["Content-Type"])
            self.assertGreater(len(r.text), 1000)
        for bad in ("../config.py", "index.html", "main.py", "..%2Fconfig.py", "app.js/../../config.py", ""):
            r = await self.dash.static(NS(cookies={COOKIE: self.cookie}, match_info={"name": bad}))
            self.assertEqual(r.status, 404, bad)

    async def test_every_operation_the_page_calls_exists_on_the_server_with_the_right_method(self):
        with open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        used_get = set(re.findall(r"\bget\('([a-z_]+)'", js))
        used_post = set(re.findall(r"\bpost\('([a-z_]+)'", js))
        self.assertTrue(used_get and used_post)
        self.assertEqual(used_get - set(self.dash._get_ops()), set(), "page calls GET ops the server does not have")
        self.assertEqual(used_post - set(self.dash._post_ops()), set(), "page calls POST ops the server does not have")
        for kind in re.findall(r"\bupload\('([a-z]+)'", js):
            self.assertIn(kind, ("photo", "file"))
        for path in re.findall(r"'(/admin/api/[a-z/]+)", js):
            self.assertTrue(any(path.startswith(p) for p in ("/admin/api/tg/", "/admin/api/upload/", "/admin/api/media", "/admin/api/avatar",
                                                             "/admin/api/events", "/admin/api/users", "/admin/api/messages")), path)

    async def test_dashboard_page_files_exist(self):
        for name in ("index.html", "app.js", "app.css"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "web", name)), name)
        with open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertEqual(html.count("__CSRF__"), 1)
        self.assertNotRegex(html, r"<script[^>]*>[^<]")                           # no inline script

    async def test_old_ai_chat_log_endpoints_still_work_through_the_new_dashboard(self):
        r = await self.dash.api_users(rq(cookie=self.cookie))
        self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main()
