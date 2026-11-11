"""A fake X for the relay's end-to-end test: OAuth 2.0 sign-in, users, search, lookups, replies.

Run: python3 relay/test/fake_x.py 8790. It records every reply in /replies (GET) for the test to check.
"""

import base64
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OWNER = {"id": "13140", "username": "taylor_", "name": "Taylor"}
JUDGE = {"id": "55", "username": "judge1", "name": "A judge"}
BOT = {"id": "999", "username": "taybotfyi", "name": "tay bot"}
POSTS = {
    "500": {"id": "500", "text": "BTC is up 900% this year", "author_id": "77", "conversation_id": "500"},
    "501": {"id": "501", "text": "@someone @taybotfyi is this right?", "author_id": OWNER["id"], "conversation_id": "500",
            "referenced_posts": [{"type": "replied_to", "id": "500"}], "display_text_range": [9, 34],
            "entities": {"mentions": [{"start": 0, "end": 8, "id": "77", "username": "someone"},
                                      {"start": 9, "end": 19, "id": BOT["id"], "username": "taybotfyi"}]}},
    "600": {"id": "600", "text": "@taybotfyi what is this?", "author_id": JUDGE["id"], "conversation_id": "600",
            "display_text_range": [0, 24], "entities": {"mentions": [{"start": 0, "end": 10, "id": BOT["id"], "username": "taybotfyi"}]}},
}
STATE = {"replies": [], "tokens": 0, "seen_auth": []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        for k, v in (headers or {"Content-Type": "application/json"}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def bearer_ok(self):
        auth = self.headers.get("Authorization", "")
        STATE["seen_auth"].append(auth)
        return auth.startswith("Bearer at")

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/authorize":  # X's consent screen, approved at once
            target = f"{q['redirect_uri']}?{urllib.parse.urlencode({'code': 'CODE123', 'state': q['state']})}"
            return self.send(302, None, {"Location": target})
        if u.path == "/replies":
            return self.send(200, STATE)
        if not self.bearer_ok():
            return self.send(401, {"title": "Unauthorized"})
        if u.path == "/2/users/me":
            return self.send(200, {"data": BOT})
        if u.path.startswith("/2/users/by/username/"):
            name = u.path.rsplit("/", 1)[1].lower()
            who = {OWNER["username"]: OWNER, JUDGE["username"]: JUDGE}.get(name)
            return self.send(200, {"data": who} if who else {"errors": [{"title": "Not Found"}]})
        if u.path == "/2/tweets/search/recent":
            if q["query"].startswith("conversation_id:"):
                return self.send(200, {"meta": {"result_count": 0}})
            if q["query"].startswith(f"from:{JUDGE['id']} "):
                return self.send(200, {"data": [POSTS["600"]], "includes": {"users": [JUDGE]}, "meta": {"newest_id": "600"}})
            return self.send(200, {"data": [POSTS["501"]], "includes": {"users": [OWNER]}, "meta": {"newest_id": "501"}})
        if u.path == "/2/tweets":
            ids = q.get("ids", "").split(",")
            return self.send(200, {"data": [POSTS[i] for i in ids if i in POSTS],
                                   "includes": {"users": [OWNER, {"id": "77", "username": "someone"}]}})
        if u.path.startswith("/2/tweets/"):
            post = POSTS.get(u.path.rsplit("/", 1)[1])
            return self.send(200, {"data": {"id": post["id"], "author_id": post["author_id"]}} if post else {"errors": []})
        return self.send(404, {"title": "Not Found"})

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if u.path == "/2/oauth2/token":
            form = dict(urllib.parse.parse_qsl(raw.decode()))
            basic = base64.b64decode(self.headers.get("Authorization", "Basic ")[6:] or b"").decode()
            if basic != "client123:secret456":
                return self.send(401, {"error": "invalid_client"})
            if form.get("grant_type") == "authorization_code" and not (form.get("code") == "CODE123" and form.get("code_verifier")):
                return self.send(400, {"error": "invalid_grant"})
            STATE["tokens"] += 1
            return self.send(200, {"access_token": f"at{STATE['tokens']}", "refresh_token": f"rt{STATE['tokens']}",
                                   "expires_in": 7200, "token_type": "bearer"})
        if not self.bearer_ok():
            return self.send(401, {"title": "Unauthorized"})
        if u.path == "/2/tweets":
            body = json.loads(raw)
            STATE["replies"].append(body)
            return self.send(201, {"data": {"id": f"r{len(STATE['replies'])}", "text": body["text"]}})
        if u.path == "/2/media/upload":
            return self.send(200, {"data": {"id": "m1"}})
        return self.send(404, {"title": "Not Found"})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1] if len(sys.argv) > 1 else 8790)), Handler).serve_forever()
