"""X API v2, signed as the bot account with OAuth 1.0a user context.

Signing is stdlib (hmac + hashlib), so the plugin adds no dependencies. The HTTP transport is
whatever ``httpx.AsyncClient``-shaped object the adapter hands in: Hermes's SSRF-safe
``platform_http_client`` in production, a fake in tests.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

API = "https://api.x.com"


def enc(value: Any) -> str:
    """RFC 3986 percent-encoding, as OAuth 1.0a requires (only unreserved characters stay)."""
    return urllib.parse.quote(str(value), safe="-._~")


@dataclass(frozen=True)
class Keys:
    key: str
    secret: str
    token: str = ""
    token_secret: str = ""

    def values(self) -> list[str]:
        """Every secret value, so the outbound guard can refuse a reply that contains one."""
        return [v for v in (self.key, self.secret, self.token, self.token_secret) if v]


def sign(method: str, url: str, params: dict, keys: Keys, *, nonce: str = "", ts: str = "") -> str:
    """OAuth 1.0a ``Authorization`` header. Query and form params are signed; JSON bodies are not."""
    oauth = {
        "oauth_consumer_key": keys.key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": ts or str(int(time.time())),
        "oauth_version": "1.0",
    }
    if keys.token:
        oauth["oauth_token"] = keys.token
    pairs = sorted((enc(k), enc(v)) for k, v in {**params, **oauth}.items())
    base = "&".join([method.upper(), enc(url), enc("&".join(f"{k}={v}" for k, v in pairs))])
    signing_key = f"{enc(keys.secret)}&{enc(keys.token_secret)}".encode()
    digest = hmac.new(signing_key, base.encode(), hashlib.sha1).digest()
    oauth["oauth_signature"] = base64.b64encode(digest).decode()
    return "OAuth " + ", ".join(f'{enc(k)}="{enc(v)}"' for k, v in sorted(oauth.items()))


class XError(Exception):
    def __init__(self, status: int, path: str, body: str, retry_after: Optional[float] = None):
        super().__init__(f"X {status} on {path}: {body[:300]}")
        self.status = status
        self.path = path
        self.body = body
        self.retry_after = retry_after


# X's 2026 spec renamed tweet to post (post.fields, referenced_posts, note_post, includes.posts).
# Ask with the new names; if X refuses them, use the old ones from then on.
NAMES = {
    "post": {
        "post.fields": "created_at,author_id,conversation_id,referenced_posts,entities,attachments,"
        "note_post,display_text_range,in_reply_to_user_id",
        "expansions": "author_id,referenced_posts.id,attachments.media_keys",
    },
    "tweet": {
        "tweet.fields": "created_at,author_id,conversation_id,referenced_tweets,entities,attachments,"
        "note_tweet,display_text_range,in_reply_to_user_id",
        "expansions": "author_id,referenced_tweets.id,attachments.media_keys",
    },
}
MORE = {"media.fields": "type,url,preview_image_url,alt_text,variants,duration_ms", "user.fields": "username,name"}


@dataclass(frozen=True)
class Relay:
    """The free trial: calls go through the summon relay, which holds the X app and the bot's tokens."""

    url: str
    key: str


class XClient:
    """The handful of X API v2 calls the bot needs. ``on_call(kind, count)`` feeds the spend meter.

    With ``relay`` set, every call goes to the relay instead of X (the trial); otherwise calls are
    signed with the user's own keys."""

    def __init__(self, keys: Keys, http: Any, *, on_call=None, relay: Optional[Relay] = None):
        self.keys = keys
        self.http = http
        self.on_call = on_call or (lambda kind, count: None)
        self.relay = relay
        self.naming = "post"
        self.free_left: Optional[int] = None  # trial replies left, as the relay last reported

    async def _via_relay(self, method: str, path: str, query: dict, body: Any, files: Any) -> dict:
        headers = {"Authorization": f"Bearer {self.relay.key}"}
        if files is not None:
            res = await self.http.request("POST", self.relay.url.rstrip("/") + "/v1/media", headers=headers, files=files)
        else:
            headers["Content-Type"] = "application/json"
            payload = {"method": method, "path": path, "query": query}
            if body is not None:
                payload["body"] = body
            res = await self.http.request("POST", self.relay.url.rstrip("/") + "/v1/x", headers=headers,
                                          content=json.dumps(payload))
        left = (getattr(res, "headers", None) or {}).get("x-summon-free-left")
        if left is not None and str(left).isdigit():
            self.free_left = int(left)
        if res.status_code >= 400:
            raise XError(res.status_code, path, res.text)
        return json.loads(res.text) if res.text else {}

    async def call(self, method: str, path: str, query: Optional[dict] = None, body: Any = None,
                   *, files: Any = None) -> dict:
        url = API + path
        query = {k: str(v) for k, v in (query or {}).items()}
        if self.relay:
            return await self._via_relay(method, path, query, body, files)
        # The query string is built here, encoded exactly as it was signed.
        full = url + ("?" + "&".join(f"{enc(k)}={enc(v)}" for k, v in query.items()) if query else "")
        headers = {"Authorization": sign(method, url, query, self.keys)}
        kwargs: dict[str, Any] = {"headers": headers}
        if body is not None:
            headers["Content-Type"] = "application/json"
            kwargs["content"] = json.dumps(body)
        if files is not None:
            kwargs["files"] = files
        res = await self.http.request(method, full, **kwargs)
        text = res.text
        if res.status_code >= 400:
            retry = None
            reset = res.headers.get("x-rate-limit-reset") if getattr(res, "headers", None) else None
            if res.status_code == 429 and reset and reset.isdigit():
                retry = max(1.0, float(reset) - time.time())
            raise XError(res.status_code, path, text, retry)
        return json.loads(text) if text else {}

    async def read(self, path: str, query: dict) -> dict:
        try:
            return await self.call("GET", path, {**query, **NAMES[self.naming], **MORE})
        except XError as e:
            if e.status != 400 or self.naming != "post" or not any(
                    s in e.body for s in ("post.fields", "referenced_posts", "note_post")):
                raise
            logger.warning("X refused the post.* field names; using tweet.* from now on")
            self.naming = "tweet"
            return await self.call("GET", path, {**query, **NAMES[self.naming], **MORE})

    async def me(self) -> dict:
        return (await self.call("GET", "/2/users/me", {"user.fields": "username,name"}))["data"]

    async def users(self, handles: list[str]) -> list[dict]:
        """Look up handles; unknown ones come back missing, not as an error."""
        if not handles:
            return []
        res = await self.call("GET", "/2/users/by", {"usernames": ",".join(handles), "user.fields": "username,name"})
        return res.get("data") or []

    async def lookup(self, ids: list[str]) -> dict:
        res = await self.read("/2/tweets", {"ids": ",".join(ids)})
        self.on_call("read", len(res.get("data") or []))
        return res

    async def search(self, query: str, **more: str) -> dict:
        res = await self.read("/2/tweets/search/recent", {"query": query, "max_results": "10", **more})
        self.on_call("read", len(res.get("data") or []))
        return res

    async def reply(self, text: str, to: str, *, exclude: Optional[list[str]] = None,
                    media: Optional[list[str]] = None) -> str:
        body: dict[str, Any] = {"text": text, "reply": {"in_reply_to_tweet_id": to}}
        if exclude:
            body["reply"]["exclude_reply_user_ids"] = exclude
        if media:
            body["media"] = {"media_ids": media}
        res = await self.call("POST", "/2/tweets", body=body)
        self.on_call("reply", 1)
        return res["data"]["id"]

    async def upload(self, data: bytes, mime: str = "image/png") -> str:
        """An image for a post. X drops it after 24 hours unless a post uses it."""
        files = {
            "media": ("image", data, mime),
            "media_category": (None, "tweet_image"),
            "media_type": (None, mime),
        }
        res = await self.call("POST", "/2/media/upload", files=files)
        return res["data"]["id"]

    async def alt(self, media_id: str, text: str) -> None:
        await self.call("POST", "/2/media/metadata", body={"id": media_id, "metadata": {"alt_text": {"text": text[:1000]}}})
