"""Tests for the summon plugin. Run: python3 -m unittest discover -s tests -v"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
import tempfile
import types
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stubs import load_plugin  # noqa: E402

PLUGIN = load_plugin()
from summon import adapter as A  # noqa: E402


def _register_with_real_hermes():
    """With real Hermes, Platform("summon") exists only once the plugin is registered, as at startup."""
    try:
        from gateway.platform_registry import PlatformEntry, platform_registry
    except ImportError:
        return
    if platform_registry.get("summon") is None:
        PLUGIN.register(types.SimpleNamespace(register_platform=lambda **kw: platform_registry.register(PlatformEntry(**kw))))


_register_with_real_hermes()
from summon import guard, split, thread  # noqa: E402
from summon.state import State  # noqa: E402
from summon.xapi import Keys, XClient, XError, sign  # noqa: E402

OWNER_ID, BOT_ID = "13140", "999"
UTC = dt.timezone.utc


class Res:
    def __init__(self, status=200, body=None, content=b"", headers=None):
        self.status_code = status
        self.text = json.dumps(body) if body is not None else ""
        self.content = content
        self.headers = headers or {}


class FakeX:
    """Routes the bot's HTTP calls to canned X responses and records what was sent."""

    def __init__(self):
        self.calls = []
        self.searches = []  # queued search responses
        self.posts = {}  # id -> post, for lookups
        self.users = {"taylor_": {"id": OWNER_ID, "username": "taylor_", "name": "Taylor"}}
        self.replies = []
        self.fail_reply = []  # statuses to fail upcoming replies with
        self.n = 0

    async def request(self, method, url, headers=None, content=None, files=None):
        u = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        self.calls.append((method, u.path, q))
        if u.path == "/2/users/me":
            return Res(body={"data": {"id": BOT_ID, "username": "taybotfyi", "name": "tay bot"}})
        if u.path == "/2/users/by":
            names = q["usernames"].lower().split(",")
            return Res(body={"data": [v for k, v in self.users.items() if k.lower() in names]})
        if u.path == "/2/tweets/search/recent":
            if q["query"].startswith("conversation_id:"):
                return Res(body={"meta": {"result_count": 0}})
            return Res(body=self.searches.pop(0) if self.searches else {"meta": {"result_count": 0}})
        if u.path == "/2/tweets" and method == "GET":
            ids = q["ids"].split(",")
            return Res(body={"data": [self.posts[i] for i in ids if i in self.posts],
                             "includes": {"users": [{"id": "77", "username": "someone", "name": "Some One"}]}})
        if u.path == "/2/tweets" and method == "POST":
            if self.fail_reply:
                return Res(self.fail_reply.pop(0), {"detail": "Your Tweet text is too long."})
            self.n += 1
            self.replies.append(json.loads(content))
            return Res(body={"data": {"id": f"r{self.n}"}})
        if u.path == "/2/media/upload":
            return Res(body={"data": {"id": "m1"}})
        if u.path == "/2/media/metadata":
            return Res(body={})
        raise AssertionError(f"unexpected call {method} {url}")

    async def get(self, url):
        return Res(content=b"\x89PNGfake", headers={"content-type": "image/png"})


def post(pid, text, author=OWNER_ID, *, reply_to=None, conversation=None, typed_from=0, mentions=(), **more):
    p = {"id": pid, "text": text, "author_id": author, "conversation_id": conversation or pid,
         "created_at": "2026-11-07T20:00:00.000Z", "display_text_range": [typed_from, len(text)],
         "entities": {"mentions": [dict(m) for m in mentions]}}
    if reply_to:
        p["referenced_posts"] = [{"type": "replied_to", "id": reply_to}]
    p.update(more)
    return p


def bot_mention(text, start=None):
    i = text.index("@taybotfyi") if start is None else start
    return {"start": i, "end": i + 10, "username": "taybotfyi", "id": BOT_ID}


ENV = {
    "X_BOT_API_KEY": "k" * 25, "X_BOT_API_SECRET": "s" * 50, "X_BOT_ACCESS_TOKEN": "13140-" + "t" * 40,
    "X_BOT_ACCESS_SECRET": "z" * 45, "X_BOT_OWNER": "@taylor_", "X_BOT_TIMEZONE": "America/New_York",
}


def make_config():
    try:
        from gateway.config import PlatformConfig
        return PlatformConfig(extra={})
    except Exception:
        return types.SimpleNamespace(extra={})


def fatal_code(a):
    stub = getattr(a, "fatal", None)
    return stub[0] if stub else getattr(a, "_fatal_error_code", None)


def collecting(a):
    """Catch what the adapter hands the gateway, with real Hermes or the stubs."""
    a.events = []

    async def collect(event):
        a.events.append(event)

    a.handle_message = collect
    return a


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_env = dict(os.environ)
        os.environ.update(ENV)
        for k in ("X_BOT_DRY_RUN", "X_BOT_POST_LIMIT", "X_BOT_DAILY_BUDGET", "X_BOT_ALLOWED_USERS"):
            os.environ.pop(k, None)
        self.fx = FakeX()
        self.dir = Path(tempfile.mkdtemp())

    async def asyncTearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)

    async def adapter(self, **env):
        os.environ.update(env)
        a = collecting(A.XAdapter(make_config()))
        ok = await a.connect(http=self.fx, data_dir=self.dir)
        a._task.cancel()  # tests drive _poll_once by hand
        self.assertTrue(ok, fatal_code(a))
        return a


class Signing(unittest.TestCase):
    def test_known_answer_from_x_docs(self):
        keys = Keys("xvz1evFS4wEEPTGEFPHBog", "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
                    "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb", "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE")
        header = sign("POST", "https://api.twitter.com/1.1/statuses/update.json",
                      {"include_entities": "true", "status": "Hello Ladies + Gentlemen, a signed OAuth request!"},
                      keys, nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", ts="1318622958")
        self.assertIn('oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"', header)


class OwnKeysSetup(unittest.TestCase):
    """Own-app setup takes pasted keys and asks X who they post as; it never runs a sign-in itself."""

    def test_whoami_signs_and_names_the_account(self):
        from unittest import mock
        from summon import setup
        seen = {}

        class Answer:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"data": {"id": BOT_ID, "username": "taybotfyi"}}).encode()

        def urlopen(req, timeout=0):
            seen["url"], seen["auth"] = req.full_url, req.get_header("Authorization")
            return Answer()

        with mock.patch.object(setup.urllib.request, "urlopen", urlopen):
            self.assertEqual(setup.whoami(Keys("k", "s", "t", "ts")), ("taybotfyi", ""))
        self.assertEqual(seen["url"], "https://api.x.com/2/users/me")
        self.assertTrue(seen["auth"].startswith("OAuth ") and 'oauth_token="t"' in seen["auth"])

    def test_whoami_reports_a_refusal(self):
        import urllib.error
        from unittest import mock
        from summon import setup

        def urlopen(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        with mock.patch.object(setup.urllib.request, "urlopen", urlopen):
            self.assertEqual(setup.whoami(Keys("k", "s", "t", "ts")), ("", "X refused those keys (401)"))

    def test_safe_defaults_limit_summons_and_hold_skills(self):
        from summon import setup
        config = {"platform_toolsets": {"telegram": ["full"]}, "skills": {"creation_nudge_interval": 15}}
        out = setup.safe_defaults(config)
        self.assertEqual(out["platform_toolsets"], {"telegram": ["full"], "summon": ["safe", "video", "skills"]})
        self.assertEqual(out["skills"], {"creation_nudge_interval": 15, "write_approval": True})
        self.assertNotIn("terminal", out["platform_toolsets"]["summon"])
        self.assertEqual(setup.safe_defaults({})["skills"], {"write_approval": True})

    def test_no_sign_in_flow_left(self):
        from summon import setup
        source = Path(setup.__file__).read_text()
        for gone in ("request_token", "access_token?", "oauth_verifier", "pin_sign_in"):
            self.assertNotIn(gone, source)


class Videos(unittest.TestCase):
    def test_smallest_mp4_with_its_length(self):
        book = thread.Book()
        book.media["3_1"] = {"media_key": "3_1", "type": "video", "duration_ms": 289096, "variants": [
            {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/pl/a.m3u8"},
            {"content_type": "video/mp4", "bit_rate": 2176000, "url": "https://video.twimg.com/1280x720/big.mp4"},
            {"content_type": "video/mp4", "bit_rate": 256000, "url": "https://video.twimg.com/480x270/small.mp4"},
            {"content_type": "video/mp4", "bit_rate": 832000, "url": "https://video.twimg.com/640x360/mid.mp4"}]}
        post = {"id": "9", "attachments": {"media_keys": ["3_1"]}}
        self.assertEqual(thread.videos(post, book),
                         ["Video (4:49 watch it with video_analyze): https://video.twimg.com/480x270/small.mp4"])
        self.assertEqual(thread.videos({"id": "8"}, book), [])


class Splitting(unittest.TestCase):
    def test_weights(self):
        self.assertEqual(split.weight("https://example.com/a/very/long/path?x=1"), 23)
        self.assertEqual(split.weight("lu.ma/abc"), 23)
        self.assertEqual(split.weight("hi 😀"), 5)

    def test_one_post_unnumbered(self):
        self.assertEqual(split.parts("short answer"), ["short answer"])

    def test_numbered_and_cut(self):
        text = "\n\n".join(f"Item {i}: " + "word " * 50 for i in range(30))
        out = split.parts(text, 280)
        self.assertEqual(len(out), 8)
        self.assertTrue(out[0].endswith("1/8"))
        self.assertIn(split.CUT, out[-1])
        self.assertTrue(all(split.weight(p) <= 280 for p in out))


class Guarding(unittest.TestCase):
    def test_leaks(self):
        self.assertTrue(guard.leaks("here: sk-ant-api03-" + "a" * 30))
        self.assertTrue(guard.leaks("token is " + "z" * 45, ["z" * 45]))
        self.assertFalse(guard.leaks("tx https://etherscan.io/tx/0x" + "ab" * 32))
        self.assertTrue(guard.leaks("key 0x" + "ab" * 32))

    def test_private_bits(self):
        self.assertEqual(guard.private_bits("mail me at a@b.co"), ["a@b.co"])
        self.assertEqual(guard.private_bits("mail a@b.co", seen="contact a@b.co"), [])

    def test_plain(self):
        self.assertEqual(guard.plain("## Hi\n**bold** [site](https://a.co) `x`"), "Hi\nbold site: https://a.co x")

    def test_notice(self):
        self.assertTrue(guard.is_notice("⏳ Queued behind the current task"))
        self.assertFalse(guard.is_notice("Yes. The chart is right."))


class Commands(unittest.TestCase):
    def test_typed_vs_added_tag(self):
        typed = post("1", "@someone @taybotfyi is this true?", typed_from=9,
                     mentions=[{"start": 0, "end": 8, "id": "77", "username": "someone"}, bot_mention("@someone @taybotfyi")])
        added = post("2", "@taybotfyi @someone lol agreed", typed_from=20, mentions=[bot_mention("@taybotfyi lol", 0)])
        self.assertTrue(thread.is_command(typed, BOT_ID, "taybotfyi"))
        self.assertFalse(thread.is_command(added, BOT_ID, "taybotfyi"))

    def test_follow_up_to_the_bot(self):
        self.assertTrue(thread.is_command(post("3", "and in euros?", in_reply_to_user_id=BOT_ID), BOT_ID, "taybotfyi"))

    def test_query_only_reads_allowed_authors(self):
        self.assertEqual(thread.commands_query(["13140"], "bot"), "from:13140 @bot -is:retweet")
        self.assertEqual(thread.commands_query(["1", "2"], "bot"), "(from:1 OR from:2) @bot -is:retweet")


class Adapter(Base):
    async def test_connect_sets_the_allowlist(self):
        a = await self.adapter()
        self.assertEqual(a._bot_handle, "taybotfyi")
        self.assertEqual(a.config.extra["allowed_users"], [OWNER_ID])
        self.assertEqual(a.resolved_allowlist_user_ids(), {OWNER_ID})

    async def test_bad_keys_fail_cleanly(self):
        async def refuse(*args, **kw):
            return Res(401, {"title": "Unauthorized"})
        self.fx.request = refuse
        a = collecting(A.XAdapter(make_config()))
        self.assertFalse(await a.connect(http=self.fx, data_dir=self.dir))
        self.assertEqual(fatal_code(a), "x_auth")

    async def summon(self, a, text="@taybotfyi is this chart right?"):
        target = {"id": "500", "text": "BTC is up 900% this year (chart)", "author_id": "77",
                  "conversation_id": "500", "created_at": "2026-11-07T19:00:00.000Z"}
        self.fx.posts["500"] = target
        cmd = post("501", "@someone " + text, reply_to="500", conversation="500", typed_from=9,
                   mentions=[{"start": 0, "end": 8, "id": "77", "username": "someone"}, bot_mention("@someone " + text)])
        self.fx.searches.append({"data": [cmd], "meta": {"newest_id": "501"}})
        return await a._poll_once()

    async def test_a_summons_reaches_the_agent_with_its_thread(self):
        a = await self.adapter()
        self.assertEqual(await self.summon(a), 1)
        ev = a.events[0]
        self.assertIn("BTC is up 900%", ev.text)
        self.assertIn("@taylor_'s request, post [2]: is this chart right?", ev.text)
        self.assertFalse(ev.allow_gateway_control)
        self.assertEqual(ev.source.chat_id, "500")
        self.assertEqual(ev.source.user_id, OWNER_ID)
        query = [c for c in self.fx.calls if c[1] == "/2/tweets/search/recent"][0][2]["query"]
        self.assertEqual(query, "from:13140 @taybotfyi -is:retweet")

    async def test_a_post_is_answered_once(self):
        a = await self.adapter()
        await self.summon(a)
        self.fx.searches.append({"data": [post("501", "@taybotfyi again")], "meta": {"newest_id": "501"}})
        self.assertEqual(await a._poll_once(), 0)
        self.assertEqual(len(a.events), 1)

    async def test_strangers_and_added_tags_are_ignored(self):
        a = await self.adapter()
        stranger = post("601", "@taybotfyi do my homework", author="66", mentions=[bot_mention("@taybotfyi do", 0)])
        added = post("602", "@taybotfyi @x nice", typed_from=14, mentions=[bot_mention("@taybotfyi @x", 0)])
        self.fx.searches.append({"data": [stranger, added], "meta": {"newest_id": "602"}})
        self.assertEqual(await a._poll_once(), 0)
        self.assertEqual(a.events, [])

    async def test_reply_goes_under_the_summoning_post(self):
        a = await self.adapter()
        await self.summon(a)
        res = await a.send("500", "**No.** It's up 90%, not 900%: https://example.com/btc", reply_to="501")
        self.assertTrue(res.success)
        self.assertEqual(self.fx.replies, [{"text": "No. It's up 90%, not 900%: https://example.com/btc",
                                            "reply": {"in_reply_to_tweet_id": "501", "exclude_reply_user_ids": ["77"]}}])
        log = a.state.recent()
        self.assertEqual(log[0]["status"], "replied")

    async def test_long_answers_split_into_numbered_replies(self):
        a = await self.adapter(X_BOT_POST_LIMIT="280")
        await self.summon(a)
        await a.send("500", "\n\n".join(f"Point {i}: " + "detail " * 30 for i in range(4)), reply_to="501")
        self.assertGreater(len(self.fx.replies), 1)
        self.assertTrue(all(r["reply"]["in_reply_to_tweet_id"] == "501" for r in self.fx.replies))
        self.assertTrue(self.fx.replies[0]["text"].endswith(f"1/{len(self.fx.replies)}"))

    async def test_one_long_post_by_default(self):
        a = await self.adapter()
        await self.summon(a)
        res = await a.send("500", "word " * 120, reply_to="501")
        self.assertTrue(res.success)
        self.assertEqual(len(self.fx.replies), 1)

    async def test_learns_280_when_x_refuses_a_long_post_and_keeps_it(self):
        a = await self.adapter()
        await self.summon(a)
        self.fx.fail_reply = [403]
        res = await a.send("500", "word " * 120, reply_to="501")
        self.assertTrue(res.success)
        self.assertGreater(len(self.fx.replies), 1)
        self.assertEqual(a.post_limit, 280)
        b = await self.adapter()  # a restart: same data folder
        self.assertEqual(b.post_limit, 280)
        c = await self.adapter(X_BOT_POST_LIMIT="4000")  # an explicit setting wins
        self.assertEqual(c.post_limit, 4000)

    async def test_nothing_goes_anywhere_else(self):
        a = await self.adapter()
        res = await a.send("123", "hello world")
        self.assertFalse(res.success)
        self.assertEqual(self.fx.replies, [])

    async def test_notices_empty_replies_and_leaks_stay_off_x(self):
        a = await self.adapter()
        await self.summon(a)
        self.assertTrue((await a.send("500", "⏳ Queued", reply_to="501")).success)
        self.assertTrue((await a.send("500", "   ", reply_to="501")).success)
        self.assertFalse((await a.send("500", "key: sk-ant-api03-" + "q" * 40, reply_to="501")).success)
        self.assertFalse((await a.send("500", "email taylor@example.com", reply_to="501")).success)
        self.assertEqual(self.fx.replies, [])

    async def test_gateway_notices_never_reach_x(self):
        """The gateway's one-time home-channel prompt is a chat-app notice: it goes to the log only."""
        a = await self.adapter()
        await self.summon(a)
        self.assertEqual(a.config.extra.get("notice_delivery"), "private")
        notice = "📬 No home channel is set for Summon.\n\nType /sethome to make this chat your home channel."
        self.assertTrue((await a.send_private_notice("500", OWNER_ID, notice)).success)
        self.assertTrue((await a.send("500", notice, reply_to="501")).success)
        self.assertEqual(self.fx.replies, [])

    async def test_images_attach_to_the_first_reply(self):
        a = await self.adapter()
        await self.summon(a)
        await a.send("500", "Here it is ![chart](https://cdn.example.com/c.png)", reply_to="501")
        self.assertEqual(self.fx.replies[0]["media"], {"media_ids": ["m1"]})
        self.assertEqual(self.fx.replies[0]["text"], "Here it is")

    async def test_dry_run_posts_nothing(self):
        a = await self.adapter(X_BOT_DRY_RUN="true")
        await self.summon(a)
        res = await a.send("500", "an answer", reply_to="501")
        self.assertTrue(res.success)
        self.assertEqual(self.fx.replies, [])

    async def test_a_reply_tags_nobody(self):
        """The other account the summons tagged is left out of the reply, and handles lose their @."""
        a = await self.adapter()
        await self.summon(a)
        res = await a.send("500", "Per @someone and (@other_guy) it's fine: https://medium.com/@writer/post", reply_to="501")
        self.assertTrue(res.success)
        sent = self.fx.replies[0]
        self.assertEqual(sent["text"], "Per someone and (other_guy) it's fine: https://medium.com/@writer/post")
        self.assertEqual(sent["reply"]["exclude_reply_user_ids"], ["77"])
        self.assertEqual(guard.untag("mail me@example.com or @@x"), "mail me@example.com or @@x")

    async def test_a_setting_that_is_not_a_number_does_not_stop_the_bot(self):
        a = await self.adapter(X_BOT_DAILY_BUDGET="a dollar", X_BOT_POLL_SECONDS="soon")
        self.assertEqual(a.budget, 1.0)
        self.assertEqual(a.poll_seconds, 20)
        b = await self.adapter(X_BOT_DAILY_BUDGET="nan", X_BOT_POLL_SECONDS="5")
        self.assertEqual(b.budget, 1.0)
        self.assertEqual(b.poll_seconds, 15)

    async def test_a_reply_that_would_pass_the_budget_is_held(self):
        a = await self.adapter(X_BOT_DAILY_BUDGET="0.05", X_BOT_POST_LIMIT="280")
        await self.summon(a)
        a.state.data["spend"] = {State.today(): 0.03}
        long = "\n\n".join(f"Point {i}: " + "detail " * 30 for i in range(4))
        self.assertGreater(len(split.parts(long, 280)), 2)
        res = await a.send("500", long, reply_to="501")
        self.assertFalse(res.success)
        self.assertIn("X_BOT_DAILY_BUDGET", res.error)
        self.assertEqual(self.fx.replies, [])
        self.assertEqual(a.state.recent()[0]["status"], "held: would pass the daily budget")
        self.assertTrue((await a.send("500", "Short answer.", reply_to="501")).success)  # one post still fits
        self.assertEqual(len(self.fx.replies), 1)

    async def test_the_280_fallback_is_held_when_it_would_pass_the_budget(self):
        a = await self.adapter(X_BOT_DAILY_BUDGET="0.05")
        await self.summon(a)
        a.state.data["spend"] = {State.today(): 0.03}
        self.fx.fail_reply = [403]  # X refuses the one long post: split at 280 would be several posts
        res = await a.send("500", "word " * 200, reply_to="501")
        self.assertFalse(res.success)
        self.assertEqual(self.fx.replies, [])
        self.assertEqual(a.post_limit, 280)

    async def test_the_budget_stops_mid_batch_and_keeps_the_rest_for_later(self):
        a = await self.adapter(X_BOT_DAILY_BUDGET="0.012")
        self.fx.posts["500"] = {"id": "500", "text": "BTC is up 900% this year (chart)", "author_id": "77",
                                "conversation_id": "500", "created_at": "2026-11-07T19:00:00.000Z"}
        first = post("501", "@someone @taybotfyi is this chart right?", reply_to="500", conversation="500", typed_from=9,
                     mentions=[{"start": 0, "end": 8, "id": "77", "username": "someone"},
                               bot_mention("@someone @taybotfyi is this chart right?")])
        second = post("601", "@taybotfyi and what about this one?", mentions=[bot_mention("@taybotfyi and what about this one?")])
        self.fx.searches.append({"data": [first, second], "meta": {"newest_id": "601"}})
        self.assertEqual(await a._poll_once(), 1)  # the search and the first thread's reads reach the cap
        self.assertTrue(a.state.handled("501"))
        self.assertFalse(a.state.handled("601"))
        self.assertEqual(a.state.since_id, "600")  # the next search starts again from the unread one
        before = len(self.fx.calls)
        self.assertEqual(await a._poll_once(), 0)  # and nothing more is read today
        self.assertEqual(len(self.fx.calls), before)

    async def test_daily_budget_stops_reads(self):
        a = await self.adapter(X_BOT_DAILY_BUDGET="0.01")
        a.state.add_spend("reply", 1)
        before = len(self.fx.calls)
        self.assertEqual(await a._poll_once(), 0)
        self.assertEqual(len(self.fx.calls), before)


class Registration(unittest.TestCase):
    def test_register_passes_only_fields_this_core_knows(self):
        import dataclasses
        from gateway.platform_registry import PlatformEntry
        got = {}
        PLUGIN.register(types.SimpleNamespace(register_platform=lambda **kw: got.update(kw)))
        self.assertEqual(got["name"], "summon")
        self.assertEqual(got["display_tier"], "minimal")
        self.assertFalse(got["allow_update_command"])
        self.assertLessEqual(set(got), {f.name for f in dataclasses.fields(PlatformEntry)})
        PlatformEntry(**got)  # and the real (or stub) entry accepts them


if __name__ == "__main__":
    unittest.main()
