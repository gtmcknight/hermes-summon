"""End to end: the plugin's setup pairs a bot through the relay (wrangler dev) against the fake X, then
the adapter finds a summons, replies through the relay, and stops when the free replies run out.

Run from the repo root: relay/test/run-e2e.sh
"""

import asyncio
import json
import sys
import threading
import types
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from _stubs import load_plugin  # noqa: E402

RELAY = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8789"
FAKE_X = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8790"

plugin = load_plugin()
from summon import adapter as A  # noqa: E402
from summon import setup  # noqa: E402

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failures.append(what)


class Res:
    def __init__(self, status, text, headers):
        self.status_code, self.text, self.headers = status, text, headers
        self.content = text.encode()


class UrllibHTTP:
    """Just enough of httpx.AsyncClient for the adapter, over urllib (JSON bodies only)."""

    async def request(self, method, url, headers=None, content=None, files=None):
        def go():
            req = urllib.request.Request(url, data=content.encode() if content else None, method=method, headers=headers or {})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return Res(r.status, r.read().decode(), {k.lower(): v for k, v in r.headers.items()})
            except urllib.error.HTTPError as e:
                return Res(e.code, e.read().decode(), {k.lower(): v for k, v in e.headers.items()})
        return await asyncio.to_thread(go)


def browser(url):
    """The user opening the link and approving on X: follows redirects like a browser would."""
    with urllib.request.urlopen(url, timeout=20) as r:
        page = r.read().decode()
    auth = url.replace("/c/", "/auth/")
    with urllib.request.urlopen(auth, timeout=20) as r:  # relay -> fake X authorize -> relay /callback
        return page, r.read().decode()


def main():
    lines = []
    result = {}

    def pair():
        result["claim"] = setup.trial("taylor_", RELAY, lines.append, wait=30, every=0.5)

    t = threading.Thread(target=pair)
    t.start()
    for _ in range(50):
        if any(l.strip().startswith("http") for l in lines):
            break
        threading.Event().wait(0.1)
    link = next(l.strip() for l in lines if l.strip().startswith("http"))
    page, done = browser(link)
    check("Sign in with X" in page and "@taylor_" in page, "connect page names the owner")
    check("is connected" in done and "@taybotfyi" in done, "callback page says the bot is connected")
    t.join(30)
    claim = result.get("claim") or {}
    check(claim.get("key", "").startswith("sk_summon_"), "setup got a trial key")
    check(claim.get("bot") == "taybotfyi" and claim.get("owner") == "taylor_", "claim names bot and owner")

    # A second claim of the same pairing hands out nothing
    import os
    os.environ.update({"X_BOT_OWNER": "taylor_", "X_BOT_RELAY_KEY": claim["key"], "X_BOT_RELAY_URL": RELAY})
    for k in ("X_BOT_API_KEY", "X_BOT_API_SECRET", "X_BOT_ACCESS_TOKEN", "X_BOT_ACCESS_SECRET"):
        os.environ.pop(k, None)

    async def run():
        import tempfile
        a = A.XAdapter(types.SimpleNamespace(extra={}))
        events = []

        async def collect(ev):
            events.append(ev)
        a.handle_message = collect
        ok = await a.connect(http=UrllibHTTP(), data_dir=Path(tempfile.mkdtemp()))
        a._task.cancel()
        check(ok and a._bot_handle == "taybotfyi", "adapter connects through the relay")
        check(await a._poll_once() == 1 and "is this right?" in events[0].text, "a summons reaches the agent")

        # The key can't be used for anything else, even with free replies left
        http = UrllibHTTP()
        hdr = {"Authorization": f"Bearer {claim['key']}", "Content-Type": "application/json"}
        bad = await http.request("POST", RELAY + "/v1/x", hdr, json.dumps(
            {"method": "GET", "path": "/2/tweets/search/recent", "query": {"query": "from:elonmusk"}}))
        check(bad.status_code == 403, f"arbitrary search refused ({bad.status_code})")
        bad = await http.request("POST", RELAY + "/v1/x", hdr, json.dumps(
            {"method": "POST", "path": "/2/tweets", "body": {"text": "spam", "reply": {"in_reply_to_tweet_id": "500"}}}))
        check(bad.status_code == 403, f"reply under someone else's post refused ({bad.status_code})")
        bad = await http.request("POST", RELAY + "/v1/x", hdr, json.dumps(
            {"method": "POST", "path": "/2/tweets", "body": {"text": "a standalone post"}}))
        check(bad.status_code == 403, f"standalone post refused ({bad.status_code})")
        r1 = await a.send("500", "No. Up 90%, not 900%.", reply_to="501")
        check(r1.success and a.x.free_left == 1, "first reply posts, one free reply left")
        r2 = await a.send("500", "Second answer.", reply_to="501")
        check(r2.success and a.x.free_left == 0, "second reply posts, none left")
        r3 = await a.send("500", "Third answer.", reply_to="501")
        code = (getattr(a, "fatal", None) or [getattr(a, "_fatal_error_code", None)])[0]
        check(not r3.success and code == "x_trial_over", f"third reply refused: trial over ({code})")


    asyncio.run(run())
    with urllib.request.urlopen(FAKE_X + "/replies") as r:
        st = json.loads(r.read())
    check(len(st["replies"]) == 2, f"X got exactly 2 replies ({len(st['replies'])})")
    check(all(x["reply"]["in_reply_to_tweet_id"] == "501" for x in st["replies"]), "both replies sit under the owner's post")
    check(not any("sk_summon_" in a for a in st["seen_auth"]), "X never saw the trial key")
    print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
