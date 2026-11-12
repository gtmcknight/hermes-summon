"""What may never go out in a public reply, whatever a thread talked the agent into. Ported from xbot."""

from __future__ import annotations

import re

# Markdown that slips through anyway, made plain for X
_MD = [
    (re.compile(r"!\[([^\]]*)\]\((\S+?)\)"), ""),  # images go out as attachments, not text
    (re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)"), r"\1: \2"),
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"__(.+?)__"), r"\1"),
    (re.compile(r"`([^`\n]+)`"), r"\1"),
    (re.compile(r"^```\w*\n?|^```$", re.M), ""),
    (re.compile(r"^#{1,6}\s+", re.M), ""),
]


def plain(text: str) -> str:
    for rx, sub in _MD:
        text = rx.sub(sub, text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# Shapes of keys and tokens
_SECRETS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-ant-[\w-]{20,}"),
    re.compile(r"\bsk-[\w-]{32,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_\w{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[\w-]{10,}"),
    re.compile(r"\bAIza[\w-]{35}\b"),
    re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"),
    # 64 hex characters outside a link: a wallet private key, not a transaction hash in an explorer URL
    re.compile(r"(?<![\w/=?&.#-])(?:0x)?[0-9a-fA-F]{64}(?![\w/])"),
]


def leaks(text: str, own: list[str] = ()) -> bool:
    """True when the text holds a known secret shape, or the actual value of one of our own keys."""
    return any(r.search(text) for r in _SECRETS) or any(v and len(v) >= 16 and v in text for v in own)


# Contact details a reply shouldn't carry unless the thread already showed them: a likely sign
# it is repeating something from the owner's private accounts
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")


def private_bits(text: str, seen: str = "") -> list[str]:
    return [s for s in _EMAIL.findall(text) + _PHONE.findall(text) if s not in seen]


# The gateway's own status lines (busy, queued, steered, saved). Useful in a chat app,
# noise in a public thread that costs a cent a post, so they never go out on X.
_NOTICE_PREFIXES = ("⚡", "⏳", "⏩", "💾", "⚠️ ", "🔄", "📬")


_TAG = re.compile(r"(?<![\w@/.])@(?=\w)")


def untag(text: str) -> str:
    """Handles without their @, so a reply tags nobody. Email addresses and links keep theirs."""
    return _TAG.sub("", text or "")


def is_notice(text: str) -> bool:
    return text.lstrip().startswith(_NOTICE_PREFIXES)


def others(post: dict, keep_out: set) -> list[str]:
    """The accounts a command post tagged, besides the owner and the bot."""
    note = post.get("note_post") or post.get("note_tweet") or {}
    mentions = ((note.get("entities") if note else None) or post.get("entities") or {}).get("mentions") or []
    seen, out = set(), []
    for m in mentions:
        i = m.get("id")
        if i and i not in keep_out and i not in seen:
            seen.add(i)
            out.append(i)
    return out
