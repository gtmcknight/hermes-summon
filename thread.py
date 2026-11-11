"""Which posts are commands, and the thread around one, as text for the agent.

Ported from xbot (github.com/gtmcknight/xbot), where these rules run live.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Book:
    """Posts, users and media from X responses. ``full`` = fetched with its own author and media."""

    posts: dict = field(default_factory=dict)
    users: dict = field(default_factory=dict)
    media: dict = field(default_factory=dict)
    full: set = field(default_factory=set)

    def add(self, res: dict) -> "Book":
        inc = res.get("includes") or {}
        for u in inc.get("users") or []:
            self.users[u["id"]] = u
        for m in inc.get("media") or []:
            self.media[m["media_key"]] = m
        for p in inc.get("posts") or inc.get("tweets") or []:
            self.posts.setdefault(p["id"], p)
        data = res.get("data") or []
        for p in data if isinstance(data, list) else [data]:
            self.posts[p["id"]] = p
            self.full.add(p["id"])
        return self


def refs(post: dict, kind: str) -> Optional[str]:
    for r in post.get("referenced_posts") or post.get("referenced_tweets") or []:
        if r.get("type") == kind:
            return r.get("id")
    return None


def _id_key(post: dict) -> int:
    return int(post["id"])


def commands_query(author_ids: list[str], bot_handle: str) -> str:
    """Only the allowed authors' posts that tag the bot. Strangers' tags are never returned,
    so spam can't reach the agent or run up the X bill."""
    who = " OR ".join(f"from:{i}" for i in author_ids)
    return f"({who}) @{bot_handle} -is:retweet" if len(author_ids) > 1 else f"{who} @{bot_handle} -is:retweet"


def is_command(post: dict, bot_id: str, bot_handle: str) -> bool:
    """A reply to one of the bot's own posts (a follow-up), or a post where the author typed the
    bot's handle. A reply starts with the handles of everyone it answers, which X adds outside the
    typed part (display_text_range), so talking to someone else in a thread the bot is in isn't one."""
    if bot_id and post.get("in_reply_to_user_id") == bot_id:
        return True
    rng = post.get("display_text_range")
    if not rng:
        return True  # X sent no range: any tag counts
    start = rng[0]
    for m in (post.get("entities") or {}).get("mentions") or []:
        if m.get("start", -1) >= start and (
                m.get("id") == bot_id or (m.get("username") or "").lower() == bot_handle.lower()):
            return True
    return False


async def _thread_of(x, book: Book, root: dict, max_posts: int = 25) -> list[dict]:
    """The rest of a thread one author wrote. A search of the conversation finds the author's posts;
    walking up from each fills any the search missed, and a reply to someone else never joins.
    X search reaches back 7 days."""
    res = await x.search(f"conversation_id:{root['conversation_id']} from:{root['author_id']}", max_results="50")
    book.add(res)
    thread = {root["id"]: root}
    data = res.get("data") or []
    for found in data if isinstance(data, list) else [data]:
        path: list[dict] = []
        p: Optional[dict] = found
        while p and len(thread) + len(path) < max_posts:
            if p["id"] in thread:
                for q in path:
                    thread[q["id"]] = q
                break
            if p.get("author_id") != root.get("author_id"):
                break
            path.append(p)
            parent = refs(p, "replied_to")
            if not parent:
                break
            if parent not in book.full:
                book.add(await x.lookup([parent]))
            p = book.posts.get(parent)
    return list(thread.values())


async def context(x, mention: dict, book: Book, depth: int = 6) -> list[dict]:
    """The posts a command sits under, oldest first, ending with the command: the replies above it,
    the whole thread when the post replied to is part of one, and any quoted posts."""
    chain = [mention]
    p = mention
    while len(chain) <= depth:
        parent = refs(p, "replied_to")
        if not parent:
            break
        if parent not in book.full:
            book.add(await x.lookup([parent]))
        p = book.posts.get(parent)
        if not p:
            break  # deleted or protected
        chain.insert(0, p)
    root = chain[0]
    if len(chain) > 1 and root.get("conversation_id") and root.get("author_id"):
        known = {q["id"] for q in chain}
        more = [q for q in await _thread_of(x, book, root) if q["id"] not in known]
        if more:
            chain = sorted(chain[:-1] + more, key=_id_key) + [mention]
    quoted = [q for q in (refs(c, "quoted") for c in chain) if q and q not in book.full]
    if quoted:
        book.add(await x.lookup(quoted))
    return chain


def body(post: dict) -> str:
    """Text with t.co links expanded and media links dropped, plus link previews."""
    note = post.get("note_post") or post.get("note_tweet") or {}
    text = note.get("text") or post.get("text") or ""
    previews = []
    for link in ((note.get("entities") if note else None) or post.get("entities") or {}).get("urls") or []:
        if link.get("media_key"):
            text = text.replace(link.get("url", ""), "")
            continue
        full = link.get("unwound_url") or link.get("expanded_url") or link.get("url", "")
        text = text.replace(link.get("url", ""), full)
        if link.get("title"):
            desc = f": {link['description']}" if link.get("description") else ""
            previews.append(f"Link: {full} ({link['title']}{desc})")
    return "\n".join([text.strip(), *previews])


def _when(created: Optional[str], tz) -> str:
    if not created:
        return ""
    try:
        t = dt.datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(tz)
    except ValueError:
        return ""
    return f"{t:%b} {t.day}, {t.strftime('%I:%M %p').lstrip('0')}"


def render(post: dict, book: Book, tz) -> str:
    u = book.users.get(post.get("author_id")) or {}
    handle = u.get("username") or "unknown"
    name = f" ({u['name']})" if u.get("name") else ""
    clips = "".join(f"\n{v}" for v in videos(post, book))
    return f"@{handle}{name}, {_when(post.get('created_at'), tz)}, https://x.com/{u.get('username') or 'i'}/status/{post['id']}\n{body(post)}{clips}"


def photos(post: dict, book: Book) -> list[str]:
    """Photo URLs, and the still for videos and GIFs."""
    out = []
    for key in (post.get("attachments") or {}).get("media_keys") or []:
        m = book.media.get(key)
        if not m:
            continue
        url = m.get("url") if m.get("type") == "photo" else m.get("preview_image_url")
        if url:
            out.append(url)
    return out


def videos(post: dict, book: Book) -> list[str]:
    """One line per video or GIF: its length and the smallest MP4 X serves, small enough for Hermes's
    video_analyze (its upstream takes about 10 MB; X's lowest bitrate is a few MB for minutes)."""
    out = []
    for key in (post.get("attachments") or {}).get("media_keys") or []:
        m = book.media.get(key) or {}
        if m.get("type") not in ("video", "animated_gif"):
            continue
        mp4 = [v for v in m.get("variants") or [] if v.get("content_type") == "video/mp4" and v.get("url")]
        if not mp4:
            continue
        smallest = min(mp4, key=lambda v: v.get("bit_rate") or v.get("bitrate") or 0)
        secs = int((m.get("duration_ms") or 0) / 1000)
        length = f"{secs // 60}:{secs % 60:02d} " if secs else ""
        out.append(f"Video ({length}watch it with video_analyze): {smallest['url']}")
    return out


_LEADING_HANDLES = re.compile(r"^(@\w+\s+)+")


def brief(chain: list[dict], book: Book, author: str, tz) -> tuple[str, list[str]]:
    """The thread as numbered posts, oldest first, then the request. Photos come back separately."""
    lines, pics = [], []
    replied_to = refs(chain[-1], "replied_to")
    for i, p in enumerate(chain, 1):
        q = book.posts.get(refs(p, "quoted") or "")
        mark = f" (the post @{author} replied to)" if p["id"] == replied_to and len(chain) > 2 else ""
        quoting = f"\nQuoting: {render(q, book, tz)}" if q else ""
        lines.append(f"[{i}]{mark} {render(p, book, tz)}{quoting}")
        pics += photos(p, book)
        if q:
            pics += photos(q, book)
    now = dt.datetime.now(tz)
    stamp = f"{now:%A, %B} {now.day}, {now.year}, {now.strftime('%I:%M %p').lstrip('0')}"
    ask = _LEADING_HANDLES.sub("", body(chain[-1]))
    text = (
        f"Now: {stamp}.\n\n<thread>\n" + "\n\n".join(lines) + "\n</thread>\n\n"
        f"@{author}'s request, post [{len(chain)}]: {ask}"
    )
    return text, pics[:6]
