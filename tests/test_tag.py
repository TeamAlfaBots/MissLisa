""".tag <msg> / .stop (mention every group member) and several owners (OWNER_IDS)."""
import asyncio
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import main
from tests.fakes import FakeMessage
from tests.test_handlers import BotCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GROUP = -1001


def member(uid, first="User", last=None, bot=False, deleted=False):
    return NS(user=NS(id=uid, first_name=first, last_name=last, username=f"u{uid}", is_bot=bot, is_deleted=deleted))


def tag_msg(text, uid=1, group=True):
    parts = text.split()
    return FakeMessage(text, uid=uid, chat_id=GROUP if group else uid, group=group, command=[parts[0].lstrip(".").lower()] + parts[1:])


class TagCase(BotCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.client.members = [member(100 + i, f"Name{i}", "Last") for i in range(12)]
        self.sleeps = []
        async def sleep(s): self.sleeps.append(s)
        p = mock.patch.object(main, "_sleep", sleep)
        p.start()
        self.addCleanup(p.stop)
        main._tag_jobs.clear()
        self.addCleanup(main._tag_jobs.clear)

    async def run_tag(self, text="hello", uid=1, wait=True):
        msg = tag_msg(f".tag {text}", uid=uid)
        await main.on_command(self.client, msg)
        if wait:
            await asyncio.gather(*list(main._tag_tasks))
        return msg

    def lines(self):
        return [l for _, text, _ in self.client.sent_messages for l in text.split("\n")]


class TestTag(TagCase):
    async def test_mentions_every_member_as_full_name_plus_message(self):
        msg = await self.run_tag("hello")
        lines = self.lines()
        self.assertEqual(len(lines), 12)
        self.assertEqual(lines[0], '<a href="tg://user?id=100">Name0 Last</a> hello')     # "Full name hello"
        self.assertEqual({chat for chat, _, _ in self.client.sent_messages}, {GROUP})
        self.assertTrue(all(mode == "html" for _, _, mode in self.client.sent_messages))
        self.assertTrue(msg.deleted)                                                      # the command message is removed

    async def test_batches_and_delays_stay_under_telegram_limits(self):
        await self.run_tag("hi")
        sizes = [len(t.split("\n")) for _, t, _ in self.client.sent_messages]
        self.assertEqual(sizes, [5, 5, 2])
        self.assertEqual(self.sleeps, [main.TAG_DELAY, main.TAG_DELAY])                   # pause between batches, none after the last
        self.assertTrue(all(len(t) < 4096 for _, t, _ in self.client.sent_messages))

    async def test_bots_deleted_accounts_and_the_account_itself_are_skipped(self):
        self.client.members = [member(1, "Me"), member(2, "Bot", bot=True), member(3, "Gone", deleted=True), member(4, "Real"),
                               NS(user=None)]
        await self.run_tag("yo")
        self.assertEqual(self.lines(), ['<a href="tg://user?id=4">Real</a> yo'])

    async def test_names_and_text_cannot_inject_markup(self):
        self.client.members = [member(9, "<b>Evil</b>", '"><script>')]
        await self.run_tag("<a href='x'>click</a> & more")
        line = self.lines()[0]
        self.assertNotIn("<b>", line)
        self.assertNotIn("<script>", line)
        self.assertNotIn("<a href='x'>", line)
        self.assertIn("&lt;b&gt;Evil&lt;/b&gt;", line)
        self.assertEqual(line.count("<a "), 1)                                            # only our own mention link

    async def test_member_without_a_name_falls_back_to_username(self):
        self.client.members = [member(5, None, None)]
        await self.run_tag("x")
        self.assertIn(">u5</a>", self.lines()[0])

    async def test_only_admins_variant(self):
        await self.run_tag("admins meeting at 5")
        self.assertEqual(self.client.member_filters[-1], "administrators")
        self.assertTrue(self.lines()[0].endswith("meeting at 5"))                          # the word 'admins' is not part of the text
        await self.run_tag("everyone hello")
        self.assertIsNone(self.client.member_filters[-1])

    async def test_cap_on_members_per_run(self):
        with mock.patch.object(main, "TAG_MAX", 7):
            await self.run_tag("x")
        self.assertEqual(len(self.lines()), 7)

    async def test_stop_halts_the_run_midway(self):
        stop = tag_msg(".stop")
        async def sleep(s):                                                               # the owner sends .stop during the pause
            self.sleeps.append(s)
            await main.on_command(self.client, stop)
        with mock.patch.object(main, "_sleep", sleep):
            await self.run_tag("hello")
        self.assertEqual(len(self.lines()), 5)                                            # only the first batch went out
        self.assertIn("5 members", stop.edits[-1] if stop.edits else stop.texts[-1])
        self.assertNotIn(GROUP, main._tag_jobs)

    async def test_stop_without_a_run_and_double_start(self):
        stop = tag_msg(".stop")
        await main.on_command(self.client, stop)
        self.assertIn("koi tagging nahi", stop.edits[-1])
        main._tag_jobs[GROUP] = {"stop": False, "sent": 0}
        again = await self.run_tag("hello", wait=False)
        self.assertIn("chal rahi hai", again.edits[-1])
        self.assertEqual(self.client.sent_messages, [])

    async def test_two_groups_can_run_at_once_and_stop_is_per_group(self):
        a = tag_msg(".tag one")
        b = FakeMessage(".tag two", uid=1, chat_id=-2002, group=True, command=["tag", "two"])
        await main.on_command(self.client, a)
        await main.on_command(self.client, b)
        self.assertEqual(set(main._tag_jobs), {GROUP, -2002})
        await main.on_command(self.client, tag_msg(".stop"))
        self.assertTrue(main._tag_jobs[GROUP]["stop"])
        self.assertFalse(main._tag_jobs[-2002]["stop"])
        await asyncio.gather(*list(main._tag_tasks))

    async def test_usage_help_private_chat_and_long_text(self):
        for text, needle in ((".tag", "Use:"), (".tag admins", "Use:"), (".tag " + "x" * 400, "300")):
            msg = tag_msg(text)
            await main.on_command(self.client, msg)
            self.assertIn(needle, msg.edits[-1], text)
        priv = tag_msg(".tag hello", group=False)
        await main.on_command(self.client, priv)
        self.assertIn("sirf groups", priv.edits[-1])
        self.assertEqual(self.client.sent_messages, [])
        self.assertEqual(main._tag_jobs, {})

    async def test_flood_wait_is_waited_out_and_the_batch_is_not_lost(self):
        from pyrogram.errors import FloodWait
        self.client.send_error = FloodWait(7)
        await self.run_tag("hello")
        self.assertEqual(len(self.lines()), 12)
        self.assertIn(8, self.sleeps)                                                     # waited value + 1

    async def test_member_list_failure_is_reported_not_crashing(self):
        self.client.member_error = RuntimeError("CHAT_ADMIN_REQUIRED")
        await self.run_tag("hello")
        self.assertEqual(len(self.client.sent_messages), 1)
        self.assertIn("admin", self.client.sent_messages[0][1])
        self.assertEqual(main._tag_jobs, {})                                              # job slot freed, can retry


class TestOwners(TagCase):
    async def test_extra_owner_can_tag_but_strangers_cannot(self):
        with mock.patch.object(main, "OWNER_IDS", [777, 888]):
            await self.run_tag("hello", uid=888)
            self.assertEqual(len(self.lines()), 12)
            self.client.sent_messages.clear()
            stranger = await self.run_tag("hello", uid=42, wait=False)
            self.assertEqual(main._tag_jobs, {})
            self.assertEqual(self.client.sent_messages, [])
            self.assertEqual(stranger.edits, [])                                          # silently ignored, not even a reply
            stop = tag_msg(".stop", uid=42)
            main._tag_jobs[GROUP] = {"stop": False, "sent": 0}
            await main.on_command(self.client, stop)
            self.assertFalse(main._tag_jobs[GROUP]["stop"])                               # a stranger cannot stop it either

    async def test_the_account_itself_is_always_an_owner(self):
        with mock.patch.object(main, "OWNER_IDS", []):
            self.assertTrue(main.is_owner(self.client, tag_msg(".tag x", uid=1)))
            self.assertFalse(main.is_owner(self.client, tag_msg(".tag x", uid=2)))
        out = tag_msg(".tag x", uid=2)
        out.outgoing = True
        self.assertTrue(main.is_owner(self.client, out))

    async def test_chatoff_still_works_for_the_existing_owner_flow(self):
        msg = FakeMessage(".chatoff", uid=1, chat_id=GROUP, group=True, command=["chatoff"])
        await main.on_command(self.client, msg)
        self.assertFalse(await main.chat_enabled(GROUP))


class TestOwnerConfig(unittest.TestCase):
    BASE = dict(API_ID="1", API_HASH="h", MONGO_URI="mongodb://u:pw@h/", DEEPSEEK_API_KEY="k", STRING_SESSION="s")

    def probe(self, **env):
        full = {k: v for k, v in os.environ.items() if not k.startswith(("OWNER", "TAG_", "BOT_", "STRING_", "ADMIN", "API_", "MONGO", "DEEPSEEK"))}
        full.update(self.BASE)
        full.update(env)
        code = "import config as c; print(c.OWNER_IDS, c.OWNER_ID, c.TAG_BATCH, c.TAG_DELAY, c.TAG_MAX)"
        return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=full, capture_output=True, text=True)

    def test_several_owner_ids_in_any_separator_and_old_single_id_merged(self):
        r = self.probe(OWNER_IDS="111111, 222222;333333 444444", OWNER_ID="222222")
        self.assertEqual(r.stdout.strip(), "[111111, 222222, 333333, 444444] 111111 5 3.0 1000")
        self.assertEqual(self.probe(OWNER_ID="555555").stdout.split()[:2], ["[555555]", "555555"])
        self.assertEqual(self.probe().stdout.split()[:2], ["[]", "0"])

    def test_bad_owner_ids_are_refused(self):
        for bad in ("abc", "12", "111111,@name", "-100123456"):
            r = self.probe(OWNER_IDS=bad)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("sahi Telegram user id nahi hai", r.stderr)

    def test_tag_settings_are_range_checked(self):
        self.assertNotEqual(self.probe(TAG_BATCH="50").returncode, 0)
        self.assertNotEqual(self.probe(TAG_DELAY="-1").returncode, 0)
        self.assertEqual(self.probe(TAG_BATCH="8", TAG_DELAY="1.5", TAG_MAX="200").stdout.split()[-3:], ["8", "1.5", "200"])


if __name__ == "__main__":
    unittest.main()
