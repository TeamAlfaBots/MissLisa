"""Lisa dataset binding, BOT_TOKEN vs STRING_SESSION modes, the dashboard master switch."""
import asyncio
import gzip
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import lisa
import main
import memory as memmod
import providers
from tests.fakes import FakeMessage, make_sticker
from tests.test_handlers import BotCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestLisaDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inst = lisa.get()

    def test_shipped_dataset_loads(self):
        self.assertIsNotNone(self.inst)
        self.assertGreater(len(self.inst.items), 10000)
        info = lisa.info()
        self.assertTrue(info["enabled"] and info["loaded"])

    def test_search_finds_relevant_examples_one_per_subtopic(self):
        for q in ("aaj mood off hai", "kal exam hai kuch nahi padha", "neend nahi aa rhi", "dost se ladai ho gayi"):
            res = self.inst.search(q, 3)
            self.assertTrue(res, q)
            self.assertLessEqual(len(res), 3)
            subs = [self.inst.items[[i for i, it in enumerate(self.inst.items) if it[2] == u and it[3] == r][0]][1] for u, r in res]
            self.assertEqual(len(subs), len(set(subs)), "examples should come from different sub-topics")
        self.assertEqual(self.inst.search("zzzzqqqq xxyy", 3), [])
        self.assertEqual(self.inst.search("", 3), [])
        self.assertEqual(self.inst.search("mood off", 0), [])

    def test_exact_same_text_is_not_returned_as_its_own_example(self):
        user = self.inst.items[0][2]
        self.assertTrue(all(u.strip().lower() != user.strip().lower() for u, _ in self.inst.search(user, 5)))

    def test_block_is_short_lowercase_and_labelled(self):
        block = self.inst.block("aaj mood off hai", 3, 120)
        self.assertIn("TONE EXAMPLES", block)
        self.assertIn("user: ", block)
        self.assertIn("tum: ", block)
        self.assertTrue(all(len(line) <= 135 for line in block.splitlines()[2:]))
        self.assertEqual(self.inst.block("zzzzqqqq", 3), "")

    def test_chatify(self):
        self.assertEqual(lisa.chatify("Haan Bilkul. Main AI hoon.", 100), "haan bilkul. main AI hoon")
        self.assertTrue(lisa.chatify("a " * 200, 50).endswith("…"))

    def test_prompt_extra_has_rules_and_examples_and_respects_the_switch(self):
        text = lisa.prompt_extra("neend nahi aa rhi")
        self.assertIn("LISA STYLE", text)
        self.assertIn("girlfriend", text)                                       # the no-romantic-partner rule is part of the prompt
        with mock.patch.object(lisa, "LISA_DATASET", False), mock.patch.object(lisa, "_instance", None):
            self.assertEqual(lisa.prompt_extra("neend nahi aa rhi"), "")

    def test_bad_or_missing_files_never_break_the_bot(self):
        missing = lisa.LisaExamples(path="/nonexistent/file.jsonl.gz")
        self.assertEqual(missing.load(), 0)
        self.assertEqual(missing.search("hello", 3), [])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.jsonl.gz")
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                fh.write("not json\n" + json.dumps({"m": ["only one"]}) + "\n" + json.dumps({"c": "a", "s": "s1", "m": ["mood off hai", "kya hua"]}) + "\n")
            part = lisa.LisaExamples(path=path)
            self.assertEqual(part.load(), 1)                                    # two broken lines skipped
            self.assertEqual(part.search("mood off", 2), [("mood off hai", "kya hua")])

    def test_dataset_has_no_leftover_template_artifacts(self):
        for cat, sub, user, reply in self.inst.items:
            for text in (user, reply):
                self.assertNotIn("{", text)
                self.assertNotIn("[1]", text)
                self.assertFalse(text != text.strip())


class TestLisaInTheBot(BotCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        main._global_state.update(value=None, ts=0.0)
        self.addCleanup(main._global_state.update, value=None, ts=0.0)

    async def test_examples_and_rules_reach_the_system_prompt(self):
        await self.say("aaj mood off hai")
        system = self.last_system()
        self.assertIn("LISA STYLE", system)
        self.assertIn("TONE EXAMPLES", system)

    async def test_dataset_can_be_switched_off(self):
        with mock.patch.object(lisa, "LISA_DATASET", False), mock.patch.object(lisa, "_instance", None):
            await self.say("aaj mood off hai")
        self.assertNotIn("TONE EXAMPLES", self.last_system())
        self.assertNotIn("LISA STYLE", self.last_system())

    async def test_build_system_prompt_appends_examples_last(self):
        out = main.build_system_prompt(SimpleNamespace(first_name="A", username=None, id=5), False, "", "", "", "\n\nEXAMPLES-HERE")
        self.assertTrue(out.endswith("EXAMPLES-HERE"))

    async def test_persona_name_defaults_to_lisa_in_this_build(self):
        import config
        self.assertTrue(config.PERSONA_NAME)                                    # tests set PERSONA_NAME=Riya; the code default is Lisa
        with open(os.path.join(ROOT, "config.py"), encoding="utf-8") as fh:
            code = fh.read()
        self.assertIn('os.getenv("PERSONA_NAME", "Lisa")', code)


class TestMasterSwitch(BotCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        main._global_state.update(value=None, ts=0.0)
        self.addCleanup(main._global_state.update, value=None, ts=0.0)

    async def test_global_off_stops_every_chat_and_on_restores(self):
        self.assertTrue((await self.say("hi")).sent)
        self.assertTrue(await main.set_global_enabled(False))
        self.assertEqual((await self.say("hello", uid=77)).sent, [])
        self.assertEqual((await self.say("hello", group=True, chat_id=-1001, mentioned=True)).sent, [])
        self.assertTrue(await main.set_global_enabled(True))
        self.assertTrue((await self.say("hello again")).sent)

    async def test_global_flag_is_stored_and_survives_a_cache_reset(self):
        await main.set_global_enabled(False)
        main._global_state.update(value=None, ts=0.0)
        self.assertFalse(await main.global_enabled())                           # read back from MongoDB
        self.assertTrue(await main.chat_enabled(42))                            # per-chat flag untouched

    async def test_global_default_is_on_and_db_failure_keeps_replying(self):
        self.assertTrue(await main.global_enabled())
        async def boom(*a, **k): raise ConnectionError("db down")
        main._global_state.update(value=None, ts=0.0)
        with mock.patch.object(main.memory, "get_global_enabled", return_value=None):
            self.assertTrue(await main.global_enabled())


class TestBotModeBehaviour(BotCase):
    async def test_owner_command_in_bot_mode_replies_instead_of_editing(self):
        with mock.patch.object(main, "MODE", "bot"):
            msg = FakeMessage("/chatoff", chat_id=-1001, group=True, command=["chatoff"])
            await main.on_command(self.client, msg)
        self.assertEqual(msg.edits, [])                                         # a bot cannot edit someone else's message
        self.assertFalse(msg.deleted)
        self.assertIn("OFF", msg.texts[0])
        self.assertFalse(await main.chat_enabled(-1001))

    async def test_slash_prefix_is_understood_by_user_commands(self):
        msg = FakeMessage("/mymemory", command=["mymemory"])
        await main.on_user_command(self.client, msg)
        self.assertTrue(msg.sent)

    async def test_stickers_command_is_unavailable_in_bot_mode(self):
        with mock.patch.object(main, "MODE", "bot"):
            msg = FakeMessage("/stickers", command=["stickers"])
            await main.on_command(self.client, msg)
        self.assertIn("STRING_SESSION", msg.texts[0])

    async def test_bot_mode_never_answers_with_a_sticker(self):
        self.client.sticker_sets[12] = []
        with mock.patch.object(main, "MODE", "bot"), mock.patch.object(main, "STICKER_REPLY_CHANCE", 1.0):
            msg = FakeMessage(None, sticker=make_sticker(1, "😂"))
            await main.on_message(self.client, msg)
        self.assertTrue(all(kind == "text" for kind, *_ in msg.sent))


class TestModeSelectionConfig(unittest.TestCase):
    TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_0123456"
    BASE = dict(API_ID="1", API_HASH="h", MONGO_URI="mongodb://u:pw@h/", DEEPSEEK_API_KEY="k")

    def probe(self, **env):
        full = {k: v for k, v in os.environ.items() if not k.startswith(("API_", "FALLBACK", "DEEPSEEK", "ADMIN", "CHAT_", "STRING_", "BOT_", "OWNER", "MONGO", "PERSONA", "LISA"))}
        full.update(self.BASE)
        full.update({k: v for k, v in env.items() if v is not None})
        code = ("import config; print(config.MODE, config.DASHBOARD_ENABLED, config.CHAT_LOG, config.PERSONA_NAME, "
                "bool(config.BOT_TOKEN), bool(config.STRING_SESSION), config.BOT_TOKEN in config.SECRETS, '' in config.SECRETS)")
        return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=full, capture_output=True, text=True)

    def test_session_only_is_userbot_mode_and_dashboard_needs_a_password(self):
        self.assertEqual(self.probe(STRING_SESSION="sess").stdout.split(),
                         ["userbot", "False", "False", "Lisa", "False", "True", "False", "False"])
        self.assertEqual(self.probe(STRING_SESSION="sess", ADMIN_PASSWORD="long-enough-pass").stdout.split()[:3], ["userbot", "True", "True"])

    def test_token_only_is_bot_mode_and_has_no_dashboard_even_with_a_password(self):
        r = self.probe(BOT_TOKEN=self.TOKEN, ADMIN_PASSWORD="long-enough-pass")
        self.assertEqual(r.stdout.split()[:3], ["bot", "False", "False"])        # dashboard + chat log are userbot-only
        self.assertEqual(r.stdout.split()[-2:], ["True", "False"])               # token is a secret; no empty secret entries

    def test_both_filled_is_refused_with_a_clear_message(self):
        r = self.probe(BOT_TOKEN=self.TOKEN, STRING_SESSION="sess")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sirf ek hi fill karo", r.stderr)

    def test_neither_filled_is_refused(self):
        r = self.probe()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("BOT_TOKEN ya STRING_SESSION me se ek zaroor fill karo", r.stderr)

    def test_malformed_bot_token_is_refused(self):
        for bad in ("notatoken", "123:short", "abc:ABCdefGHIjklMNOpqrSTUvwxYZ"):
            r = self.probe(BOT_TOKEN=bad)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("BOT_TOKEN ka format galat hai", r.stderr)

    def test_quotes_and_blank_values_are_tolerated(self):
        self.assertEqual(self.probe(STRING_SESSION='"sess"', BOT_TOKEN="").stdout.split()[0], "userbot")   # empty BOT_TOKEN = not filled
        self.assertEqual(self.probe(BOT_TOKEN=f"'{self.TOKEN}'").stdout.split()[0], "bot")


class TestClientCreationPerMode(unittest.IsolatedAsyncioTestCase):
    async def run_main(self, mode):
        stubs = sys.modules["tests"].stubs
        stubs.Client = sys.modules["pyrogram"].Client
        stubs.Client.instances.clear()
        sys.modules["aiohttp.web"].AppRunner.instances.clear()
        event = stubs.reset_idle()
        main.state = main.HealthState() if hasattr(main, "HealthState") else main.state
        main.state.providers_status = providers.providers_status
        patches = [mock.patch.object(main, "MODE", mode), mock.patch.object(main, "DASHBOARD_ENABLED", mode == "userbot"),
                   mock.patch.object(main, "BOT_TOKEN", "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_0123456" if mode == "bot" else ""),
                   mock.patch.object(main, "OWNER_IDS", [555, 666] if mode == "bot" else [])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        task = asyncio.create_task(main.main())
        for _ in range(100):
            await asyncio.sleep(0.01)
            if main.state.ready:
                break
        client = stubs.Client.instances[-1]
        event.set()
        await asyncio.wait_for(task, 5)
        return client

    async def test_userbot_mode_uses_the_session_string_and_registers_dashboard_hooks(self):
        client = await self.run_main("userbot")
        self.assertIn("session_string", client.kw)
        self.assertNotIn("bot_token", client.kw)
        self.assertIn("on_any_message", [h.callback.__name__ for h in client.handlers])

    async def test_bot_mode_uses_the_token_and_has_no_dashboard_hooks(self):
        client = await self.run_main("bot")
        self.assertEqual(client.kw["bot_token"], "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_0123456")
        self.assertNotIn("session_string", client.kw)
        names = [h.callback.__name__ for h in client.handlers]
        self.assertNotIn("on_any_message", names)
        self.assertEqual(names, ["on_command", "on_user_command", "on_message"])
        self.assertEqual(client.handlers[0].filters is not None, True)


if __name__ == "__main__":
    unittest.main()
