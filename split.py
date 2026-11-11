"""Fitting a reply into X posts. Ported from xbot."""

from __future__ import annotations

import re

# X counts every link as 23 characters and most non-Latin characters and emoji as 2.
# Bare domains like lu.ma/abc become links too, so anything domain-shaped counts as one.
_LINK = re.compile(r"https?://\S+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?", re.I)

CUT = "(cut here: too long for X)"


def _light(c: int) -> bool:
    return c <= 4351 or 8192 <= c <= 8205 or 8208 <= c <= 8223 or 8242 <= c <= 8247


def weight(text: str) -> int:
    n = 0

    def link(_m):
        nonlocal n
        n += 23
        return ""

    for ch in _LINK.sub(link, text):
        n += 1 if _light(ord(ch)) else 2
    return n


def _pack(text: str, limit: int) -> list[str]:
    """Pack into posts by paragraph, so a heading stays with its items; a paragraph too long for one
    post goes by lines, and a line too long breaks between words."""
    posts: list[str] = []
    cur = ""

    def flush():
        nonlocal cur
        if cur.strip():
            posts.append(cur.strip())
        cur = ""

    for para in re.split(r"\n\s*\n", text.strip()):
        joined = f"{cur}\n\n{para}" if cur else para
        if weight(joined) <= limit:
            cur = joined
            continue
        flush()
        if weight(para) <= limit:
            cur = para
            continue
        for line in para.split("\n"):
            joined = f"{cur}\n{line}" if cur else line
            if weight(joined) <= limit:
                cur = joined
                continue
            flush()
            words = line.split(" ")
            while words:
                head = words.pop(0)
                if weight(head) > limit:
                    head = head[: limit >> 1]
                while words and weight(f"{head} {words[0]}") <= limit:
                    head += f" {words.pop(0)}"
                if words:
                    posts.append(head)
                else:
                    cur = head
    flush()
    return posts


def parts(text: str, limit: int = 280, max_posts: int = 8) -> list[str]:
    """The reply as posts, numbered when there is more than one, cut with a note past ``max_posts``."""
    one = _pack(text, limit)
    if len(one) <= 1:
        return one
    many = _pack(text, limit - 6)
    if len(many) > max_posts:
        last = _pack(many[max_posts - 1], limit - 7 - weight(CUT))[0]
        many = many[: max_posts - 1] + [f"{last}\n{CUT}"]
    return [f"{p}\n{i}/{len(many)}" for i, p in enumerate(many, 1)]


def room(limit: int, max_posts: int = 8) -> int:
    """Roughly how many characters fit in a reply: one long post when the account allows them,
    else what eight 280-character posts hold."""
    return limit if limit > 280 else int(((limit - 6) * max_posts * 0.85) // 100 * 100)
