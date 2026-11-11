"""``hermes gateway setup summon``: connect the bot account, then keep X summons to safe tools.

Two ways in:
  1. Free trial. Open a link on any device, sign in to X as the bot, done. Calls go through the summon
     relay (it holds the X app), free for the first replies.
  2. Your own X app. Paste four keys from console.x.com, made while signed in as the bot. Keeps going
     after the trial; you pay X directly (about a cent a reply). Setup never runs an X sign-in itself:
     Hermes owns OAuth sign-ins, and its plugin route can't reach X yet.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .xapi import API, Keys, sign

DEFAULT_RELAY = "https://summon.taylor.fyi"
_UA = {"User-Agent": "hermes-summon-setup"}


def _post_json(url: str, body: dict, timeout: float = 20) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **_UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return {"status": "error", **json.loads(e.read() or b"{}"), "http": e.code}
        except ValueError:
            return {"status": "error", "http": e.code}


def trial(owner: str, relay: str, out, wait: float = 900, every: float = 3, borrow: bool = False) -> dict | None:
    """Pair a bot through the relay. ``out`` gets the lines to show. Returns the claim or None.
    ``borrow`` skips the sign-in: the relay lends its shared bot for a few replies under the owner's posts."""
    started = _post_json(relay.rstrip("/") + "/v1/pair", {"owner": owner, "borrow": True} if borrow else {"owner": owner})
    if borrow:
        if started.get("status") == "done":
            return started
        out(f"Borrowing isn't available: {started.get('message') or started}")
        return None
    if not started.get("pair"):
        out(f"The trial isn't available right now: {started.get('message') or started}")
        return None
    out(f"Open this link on any device, signed in to X as your bot account (not @{owner}):")
    out(f"  {started['url']}")
    out("Waiting for the sign-in (Ctrl-C to stop)...")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        claim = _post_json(f"{relay.rstrip('/')}/v1/pair/{started['pair']}/claim", {})
        if claim.get("status") == "done":
            return claim
        if claim.get("status") == "expired" or claim.get("http") == 410:
            out("The link expired. Run setup again for a new one.")
            return None
        time.sleep(every)
    out("Timed out waiting for the sign-in.")
    return None


def whoami(keys: Keys) -> tuple[str, str]:
    """(handle, "") for the account these keys post as, or ("", why) when X refuses them."""
    url = f"{API}/2/users/me"
    req = urllib.request.Request(url, headers={"Authorization": sign("GET", url, {}, keys), **_UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return (json.loads(res.read() or b"{}").get("data") or {}).get("username", ""), ""
    except urllib.error.HTTPError as e:
        return "", f"X refused those keys ({e.code})"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return "", f"couldn't reach X ({e})"


# What X summons may use: web search, reading and making images, watching videos, and skills. Skills are the one write,
# so setup also turns on Hermes's skill approval: a skill learned from X waits for the owner's OK.
SAFE_TOOLSETS = ["safe", "video", "skills"]


def safe_defaults(config: dict) -> dict:
    """Limit X summons to SAFE_TOOLSETS and hold every new skill for approval (/skills pending)."""
    toolsets = config.get("platform_toolsets")
    if not isinstance(toolsets, dict):
        toolsets = config["platform_toolsets"] = {}
    toolsets["summon"] = list(SAFE_TOOLSETS)
    skills = config.get("skills")
    if not isinstance(skills, dict):
        skills = config["skills"] = {}
    skills["write_approval"] = True
    return config


def run() -> None:
    # Lazy, so the plugin imports outside the CLI; only helpers Hermes documents for plugins.
    from hermes_cli.cli_output import print_header, print_info, print_success, print_warning
    from hermes_cli.config import get_env_value, save_env_value
    from hermes_cli.setup import prompt, prompt_choice, prompt_yes_no

    print_header("X (summon)")
    print_info("Tag your bot under any post on X and your Hermes answers in the thread.")
    print_info("You need a second X account for the bot. Only you can summon it.")
    owner = prompt("Your X handle", default=get_env_value("X_BOT_OWNER") or "").strip().lstrip("@")
    if not owner:
        print_warning("No handle, nothing saved.")
        return
    save_env_value("X_BOT_OWNER", owner)

    choice = prompt_choice("How should the bot reach X?", [
        "Free trial: sign in with X as your bot account (no keys, first replies free)",
        "Borrow @taybotfyi: no bot account needed, it answers only under your posts (demo, 10 replies)",
        "My own X app: keeps going after the trial, you pay X about a cent a reply",
    ], default=0)

    if choice in (0, 1):
        relay = get_env_value("X_BOT_RELAY_URL") or DEFAULT_RELAY
        claim = trial(owner, relay, print_info, borrow=(choice == 1))
        if not claim:
            return
        save_env_value("X_BOT_RELAY_KEY", claim["key"])
        print_success(f"@{claim['bot']} is connected and answers @{claim['owner']}. "
                      f"{claim.get('free_left', 10)} free replies.")
        if get_env_value("X_BOT_API_KEY"):
            print_warning("Your own X keys are also set, and they win over the trial. Clear X_BOT_API_KEY to use the trial.")
    else:
        print_info("Sign in to console.x.com as the bot account and make an app in a Pay Per Use project.")
        print_info("Set its permissions to Read and write first, then copy four values from Keys and tokens")
        print_info("(generate the access token after the permissions change, or it can only read).")
        values = {env: prompt(label, password=True).strip() for env, label in (
            ("X_BOT_API_KEY", "Consumer key"), ("X_BOT_API_SECRET", "Secret key"),
            ("X_BOT_ACCESS_TOKEN", "Access token"), ("X_BOT_ACCESS_SECRET", "Access token secret"))}
        if not all(values.values()):
            print_warning("All four are needed, nothing saved.")
            return
        bot, why = whoami(Keys(*values.values()))
        if not bot:
            print_warning(f"{why or 'X returned no account'}. Check the four values and run setup again.")
            return
        if bot.lower() == owner.lower():
            print_warning(f"Those keys post as @{owner}. Make them signed in as the bot account and run setup again.")
            return
        for env, value in values.items():
            save_env_value(env, value)
        print_success(f"@{bot} is connected with your own X app and answers @{owner}.")


    print_info("Recommended: X summons get web search, images, video and skills, and nothing else (no terminal or files).")
    print_info("A skill it learns from X waits for your OK before your agent uses it. This turns on skill")
    print_info("approval for every chat: review new skills with /skills pending in Hermes.")
    if prompt_yes_no("Use these limits?", True):
        try:
            from hermes_cli.config import load_config, save_config
            save_config(safe_defaults(load_config()), merge_existing=True)
            print_success("Done. Change the tools any time with: hermes tools")
        except Exception as e:
            print_warning(f"Couldn't set them ({e}). In config.yaml set platform_toolsets.summon: [safe, video, skills] "
                          "and skills.write_approval: true.")
    print_info("Start it with: hermes gateway")
