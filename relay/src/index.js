// Summon relay: the free trial for the summon Hermes plugin.
//
// A user signs in to X as their bot account through this relay's X app, and the plugin gets a key.
// With that key the plugin can do exactly what summon needs (find the owner's summons, read the thread
// around one, reply under the owner's post) and nothing else, for FREE_REPLIES replies per bot. After
// that the plugin switches to the user's own X keys. The X app's secret and the bots' tokens never
// leave this Worker. Everything runs in one Durable Object, so token refreshes never race.

import { DurableObject } from 'cloudflare:workers';

const SCOPES = 'tweet.read tweet.write users.read offline.access media.write';
const PAIR_TTL = 15 * 60_000;
const HANDLE = /^[A-Za-z0-9_]{1,15}$/;
const ID = /^\d{1,20}$/;
const MAX_MEDIA = 5 * 1024 * 1024;

export default {
  async fetch(req, env) {
    return env.STORE.get(env.STORE.idFromName('global')).fetch(req);
  },
};

const json = (body, status = 200, headers = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', ...headers } });

const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const random = (n) => b64url(crypto.getRandomValues(new Uint8Array(n)));
const sha256 = async (s) => b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)));
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const today = () => new Date().toISOString().slice(0, 10);

function code() {
  const abc = 'ABCDEFGHJKMNPQRSTVWXYZ23456789';
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  const s = [...bytes].map((b) => abc[b % abc.length]).join('');
  return `${s.slice(0, 4)}-${s.slice(4)}`;
}

function page(title, body) {
  return new Response(
    `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(title)}</title>
<style>
:root{--bg:#fff;--ink:#0f1419;--soft:#536471;--line:#eff3f4;--btn:#0f1419;--btnInk:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#000;--ink:#e7e9ea;--soft:#71767b;--line:#2f3336;--btn:#eff3f4;--btnInk:#0f1419}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:420px;margin:12vh auto;padding:0 16px}
h1{font-size:24px;margin:0 0 12px}p{color:var(--soft);margin:0 0 16px}b{color:var(--ink)}
a.btn{display:inline-block;background:var(--btn);color:var(--btnInk);text-decoration:none;font-weight:700;padding:12px 22px;border-radius:999px}
code{font-size:15px}
</style><main>${body}</main>`,
    { headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' } },
  );
}

export class Store extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.env = env;
    this.sql = ctx.storage.sql;
    this.sql.exec(`CREATE TABLE IF NOT EXISTS pairs (id TEXT PRIMARY KEY, code TEXT UNIQUE, owner TEXT, state TEXT,
      verifier TEXT, created INTEGER, result TEXT)`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS bots (key_hash TEXT PRIMARY KEY, bot_id TEXT, bot_handle TEXT,
      owner_id TEXT, owner_handle TEXT, access TEXT, refresh TEXT, expires INTEGER, free_left INTEGER,
      day TEXT, reads INTEGER, created INTEGER)`);
    // Borrowed keys: a row whose token_of names the bot_id of the signed-in row that holds the X tokens.
    if (!this.sql.exec("SELECT 1 FROM pragma_table_info('bots') WHERE name = 'token_of'").toArray().length) {
      this.sql.exec('ALTER TABLE bots ADD COLUMN token_of TEXT');
    }
    this.sql.exec(`CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)`);
  }

  get api() { return this.env.X_API || 'https://api.x.com'; }
  get free() { return Number(this.env.FREE_REPLIES || 10); }
  get readsPerDay() { return Number(this.env.READS_PER_DAY || 300); }

  meta(k, fallback = '0') {
    return this.sql.exec('SELECT v FROM meta WHERE k = ?', k).toArray()[0]?.v ?? fallback;
  }
  setMeta(k, v) { this.sql.exec('INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)', k, String(v)); }

  async fetch(req) {
    const url = new URL(req.url);
    const origin = this.env.PUBLIC_ORIGIN || url.origin;
    const p = url.pathname;
    try {
      if (req.method === 'GET' && p === '/') return this.home();
      if (req.method === 'POST' && p === '/v1/pair') return await this.pair(req, origin);
      let m;
      if (req.method === 'GET' && (m = p.match(/^\/c\/([A-Z0-9-]{9})$/))) return this.connectPage(m[1]);
      if (req.method === 'GET' && (m = p.match(/^\/auth\/([A-Z0-9-]{9})$/))) return await this.auth(m[1], origin);
      if (req.method === 'GET' && p === '/callback') return await this.callback(url, origin);
      if (req.method === 'POST' && (m = p.match(/^\/v1\/pair\/([\w-]{20,40})\/claim$/))) return this.claim(m[1]);
      if (p.startsWith('/v1/')) {
        const bot = await this.bot(req);
        if (!bot) return json({ error: 'unauthorized', message: 'Unknown summon key. Run: hermes gateway setup summon' }, 401);
        if (req.method === 'GET' && p === '/v1/status') return json(this.status(bot));
        if (req.method === 'POST' && p === '/v1/x') return await this.proxy(bot, await req.json());
        if (req.method === 'POST' && p === '/v1/media') return await this.media(bot, req);
      }
      return json({ error: 'not_found' }, 404);
    } catch (e) {
      console.error(e);
      return json({ error: 'relay_error', message: String(e?.message || e) }, 500);
    }
  }

  home() {
    return page('Summon', `<h1>Summon</h1><p>Your own @grok on X, powered by your Hermes agent. Tag your bot under any
      post and your agent answers in the thread.</p><p>This page signs bots in for the free trial. Start from Hermes:
      <code>hermes gateway setup summon</code></p>`);
  }

  // ── sign-in ───────────────────────────────────────────────────────────────

  async pair(req, origin) {
    const { owner, borrow } = await req.json().catch(() => ({}));
    const handle = String(owner || '').replace(/^@/, '');
    if (!HANDLE.test(handle)) return json({ error: 'bad_owner', message: 'owner must be an X handle' }, 400);
    if (Number(this.meta('replies_given')) >= Number(this.env.TRIAL_POOL || 2000)) {
      return json({ error: 'trial_closed', message: 'The free trial is full. Use your own X keys.' }, 403);
    }
    if (borrow) return await this.borrow(handle);
    this.sql.exec('DELETE FROM pairs WHERE created < ?', Date.now() - PAIR_TTL);
    if (this.sql.exec('SELECT COUNT(*) AS n FROM pairs').toArray()[0].n >= 500) {
      return json({ error: 'busy', message: 'Too many sign-ins in progress. Try again in a few minutes.' }, 429);
    }
    const id = random(18), c = code();
    this.sql.exec('INSERT INTO pairs (id, code, owner, created) VALUES (?, ?, ?, ?)', id, c, handle, Date.now());
    return json({ pair: id, code: c, url: `${origin}/c/${c}`, expires_in: PAIR_TTL / 1000 });
  }

  // The demo path: no bot account needed. The owner's Hermes answers as the shared bot (BORROW_BOT, signed in
  // once by its owner) under the owner's own posts only, for BORROW_FREE replies. The key gets no tokens of its
  // own; the proxy uses the signed-in row's, so a refresh never races between borrowers.
  async borrow(handle) {
    const shared = String(this.env.BORROW_BOT || '').replace(/^@/, '').toLowerCase();
    const primary = shared && this.sql.exec(
      'SELECT * FROM bots WHERE lower(bot_handle) = ? AND token_of IS NULL ORDER BY created DESC LIMIT 1', shared).toArray()[0];
    if (!primary) return json({ error: 'no_borrow', message: 'Borrowing a bot is not available right now. Sign in with your own bot account.' }, 503);
    const res = await this.x(primary, 'GET', `/2/users/by/username/${encodeURIComponent(handle)}`);
    const owner = res.ok ? (await res.json())?.data : null;
    if (!owner?.id) return json({ error: 'no_owner', message: `@${handle} isn't on X` }, 404);
    if (owner.id === primary.bot_id) return json({ error: 'bad_owner', message: 'That is the shared bot itself' }, 400);
    const prior = this.sql.exec('SELECT MIN(free_left) AS f FROM bots WHERE bot_id = ? AND owner_id = ? AND token_of IS NOT NULL',
      primary.bot_id, owner.id).toArray()[0]?.f;
    const free = Math.min(prior ?? Infinity, Number(this.env.BORROW_FREE || 10));
    this.sql.exec('DELETE FROM bots WHERE bot_id = ? AND owner_id = ? AND token_of IS NOT NULL', primary.bot_id, owner.id);
    const key = `sk_summon_${random(24)}`;
    this.sql.exec(
      `INSERT INTO bots (key_hash, bot_id, bot_handle, owner_id, owner_handle, access, refresh, expires, free_left, day, reads, created, token_of)
       VALUES (?, ?, ?, ?, ?, '', '', 0, ?, ?, 0, ?, ?)`,
      await sha256(key), primary.bot_id, primary.bot_handle, owner.id, owner.username, free, today(), Date.now(), primary.bot_id);
    return json({ status: 'done', key, bot: primary.bot_handle, bot_id: primary.bot_id, owner: owner.username, owner_id: owner.id,
      free_left: free, borrowed: true });
  }

  livePair(where, value) {
    const row = this.sql.exec(`SELECT * FROM pairs WHERE ${where} = ?`, value).toArray()[0];
    return row && row.created > Date.now() - PAIR_TTL ? row : null;
  }

  connectPage(c) {
    const pair = this.livePair('code', c);
    if (!pair || pair.result) return page('Link expired', '<h1>This link expired</h1><p>Run <code>hermes gateway setup summon</code> again for a new one.</p>');
    return page('Connect your bot', `<h1>Connect your bot</h1>
      <p>Sign in to X as the <b>bot account</b> that will answer <b>@${esc(pair.owner)}</b>, not as @${esc(pair.owner)}.
      If you're signed in to X as yourself, switch accounts first.</p>
      <p>Your first ${this.free} replies are free.</p>
      <p><a class="btn" href="/auth/${esc(c)}">Sign in with X</a></p>`);
  }

  async auth(c, origin) {
    const pair = this.livePair('code', c);
    if (!pair || pair.result) return this.connectPage(c);
    const state = random(24), verifier = random(48);
    const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
    this.sql.exec('UPDATE pairs SET state = ?, verifier = ? WHERE id = ?', state, verifier, pair.id);
    const q = new URLSearchParams({
      response_type: 'code', client_id: this.env.X_CLIENT_ID, redirect_uri: `${origin}/callback`, scope: SCOPES,
      state, code_challenge: challenge, code_challenge_method: 'S256',
    });
    return Response.redirect(`${this.env.X_AUTH_URL || 'https://x.com/i/oauth2/authorize'}?${q}`, 302);
  }

  async token(form) {
    const res = await fetch(`${this.api}/2/oauth2/token`, {
      method: 'POST',
      headers: {
        'content-type': 'application/x-www-form-urlencoded',
        authorization: 'Basic ' + btoa(`${this.env.X_CLIENT_ID}:${this.env.X_CLIENT_SECRET}`),
      },
      body: new URLSearchParams({ ...form, client_id: this.env.X_CLIENT_ID }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok || !body.access_token) throw new Error(`X token ${res.status}: ${JSON.stringify(body).slice(0, 200)}`);
    return body;
  }

  async callback(url, origin) {
    const state = url.searchParams.get('state') || '';
    const pair = state && this.sql.exec('SELECT * FROM pairs WHERE state = ?', state).toArray()[0];
    if (!pair || pair.created < Date.now() - PAIR_TTL || pair.result) {
      return page('Link expired', '<h1>This sign-in expired</h1><p>Run <code>hermes gateway setup summon</code> again.</p>');
    }
    if (url.searchParams.get('error')) return page('Not connected', '<h1>Not connected</h1><p>X sign-in was cancelled. Open the link from Hermes to try again.</p>');
    const tok = await this.token({
      grant_type: 'authorization_code', code: url.searchParams.get('code') || '', redirect_uri: `${origin}/callback`,
      code_verifier: pair.verifier,
    });
    const auth = { authorization: `Bearer ${tok.access_token}` };
    const me = (await (await fetch(`${this.api}/2/users/me`, { headers: auth })).json()).data;
    const owner = (await (await fetch(`${this.api}/2/users/by/username/${encodeURIComponent(pair.owner)}`, { headers: auth })).json()).data;
    if (!me?.id) throw new Error('X did not say which account signed in');
    if (!owner?.id) return page('No such account', `<h1>@${esc(pair.owner)} isn't on X</h1><p>Check the handle in Hermes and run setup again.</p>`);
    if (me.id === owner.id) {
      return page('Wrong account', `<h1>That's @${esc(me.username)}</h1><p>Sign in as the <b>bot</b> account instead. In X, switch
        accounts, then <a href="/c/${esc(pair.code)}">try again</a>.</p>`);
    }
    // A bot keeps what's left of its trial across sign-ins, so signing in again never resets it.
    const prior = this.sql.exec('SELECT MIN(free_left) AS f FROM bots WHERE bot_id = ?', me.id).toArray()[0]?.f;
    const free = prior ?? this.free;
    this.sql.exec('DELETE FROM bots WHERE bot_id = ? AND token_of IS NULL', me.id);
    const key = `sk_summon_${random(24)}`;
    this.sql.exec(
      `INSERT INTO bots (key_hash, bot_id, bot_handle, owner_id, owner_handle, access, refresh, expires, free_left, day, reads, created)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)`,
      await sha256(key), me.id, me.username, owner.id, owner.username, tok.access_token, tok.refresh_token || '',
      Date.now() + (Number(tok.expires_in) || 7200) * 1000, free, today(), Date.now(),
    );
    const result = { key, bot: me.username, bot_id: me.id, owner: owner.username, owner_id: owner.id, free_left: free };
    this.sql.exec('UPDATE pairs SET result = ? WHERE id = ?', JSON.stringify(result), pair.id);
    return page('Connected', `<h1>@${esc(me.username)} is connected</h1><p>It will answer <b>@${esc(owner.username)}</b>.
      ${free} free replies left. Go back to Hermes: setup finishes by itself.</p>`);
  }

  claim(id) {
    const pair = this.livePair('id', id);
    if (!pair) return json({ status: 'expired' }, 410);
    if (!pair.result) return json({ status: 'pending' });
    this.sql.exec('DELETE FROM pairs WHERE id = ?', id); // the key is handed out once
    return json({ status: 'done', ...JSON.parse(pair.result) });
  }

  // ── the bot's calls ───────────────────────────────────────────────────────

  async bot(req) {
    const key = (req.headers.get('authorization') || '').replace(/^Bearer\s+/i, '');
    if (!key.startsWith('sk_summon_')) return null;
    return this.sql.exec('SELECT * FROM bots WHERE key_hash = ?', await sha256(key)).toArray()[0] || null;
  }

  status(bot) {
    return { bot: bot.bot_handle, owner: bot.owner_handle, free_left: bot.free_left };
  }

  tokenRow(bot) {
    if (!bot.token_of) return bot;
    const row = this.sql.exec('SELECT * FROM bots WHERE bot_id = ? AND token_of IS NULL ORDER BY created DESC LIMIT 1', bot.token_of).toArray()[0];
    if (!row) throw new Error('The shared bot is no longer signed in');
    return row;
  }

  async access(bot, force = false) {
    const t = this.tokenRow(bot);
    if (!force && t.expires > Date.now() + 60_000) return t.access;
    if (!t.refresh) throw new Error('X sign-in expired. Run: hermes gateway setup summon');
    const tok = await this.token({ grant_type: 'refresh_token', refresh_token: t.refresh });
    t.access = tok.access_token;
    t.refresh = tok.refresh_token || t.refresh;
    t.expires = Date.now() + (Number(tok.expires_in) || 7200) * 1000;
    this.sql.exec('UPDATE bots SET access = ?, refresh = ?, expires = ? WHERE key_hash = ?', t.access, t.refresh, t.expires, t.key_hash);
    return t.access;
  }

  async x(bot, method, path, query, body, retry = true) {
    const qs = query && Object.keys(query).length ? `?${new URLSearchParams(query)}` : '';
    const res = await fetch(`${this.api}${path}${qs}`, {
      method,
      headers: { authorization: `Bearer ${await this.access(bot)}`, ...(body !== undefined && { 'content-type': 'application/json' }) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401 && retry && this.tokenRow(bot).refresh) {
      await this.access(bot, true);
      return this.x(bot, method, path, query, body, false);
    }
    return res;
  }

  countReads(bot, n) {
    const day = today();
    const reads = (bot.day === day ? bot.reads : 0) + n;
    this.sql.exec('UPDATE bots SET day = ?, reads = ? WHERE key_hash = ?', day, reads, bot.key_hash);
    const all = (this.meta(`reads_${day}`) | 0) + n;
    this.setMeta(`reads_${day}`, all);
  }

  overReads(bot) {
    const day = today();
    return (bot.day === day && bot.reads >= this.readsPerDay) || (this.meta(`reads_${day}`) | 0) >= Number(this.env.READS_POOL_PER_DAY || 20000);
  }

  // Exactly the calls summon makes. Anything else is refused, so a key can't be used as a general X client.
  async proxy(bot, req) {
    const trialOver = json({
      error: 'trial_over',
      message: `@${bot.bot_handle} used its ${this.free} free replies. Add your own X keys: hermes gateway setup summon`,
    }, 402, { 'x-summon-free-left': '0' });
    if (bot.free_left <= 0) return trialOver;
    const { method, path, query = {}, body } = req || {};
    const head = { 'x-summon-free-left': String(bot.free_left) };

    if (method === 'GET' && path === '/2/users/me') return json({ data: { id: bot.bot_id, username: bot.bot_handle } }, 200, head);
    if (method === 'GET' && path === '/2/users/by') {
      const names = String(query.usernames || '').split(',').map((s) => s.trim().toLowerCase());
      const data = names.includes(bot.owner_handle.toLowerCase()) ? [{ id: bot.owner_id, username: bot.owner_handle }] : [];
      return json({ data }, 200, head);
    }
    if (method === 'GET' && (path === '/2/tweets/search/recent' || path === '/2/tweets')) {
      if (path === '/2/tweets/search/recent') {
        const q = String(query.query || '');
        const own = q === `from:${bot.owner_id} @${bot.bot_handle} -is:retweet`;
        const thread = /^conversation_id:\d{1,20} from:\d{1,20}$/.test(q);
        if (!own && !thread) return json({ error: 'refused', message: 'summon only searches for its owner\'s summons and their threads' }, 403);
      } else if (!String(query.ids || '').split(',').every((i) => ID.test(i)) || String(query.ids).split(',').length > 10) {
        return json({ error: 'refused', message: 'look up at most 10 posts by ID' }, 403);
      }
      if (this.overReads(bot)) return json({ title: 'Too Many Requests', detail: 'summon trial read limit for today' }, 429);
      const res = await this.x(bot, 'GET', path, query);
      const text = await res.text();
      let n = 0;
      try { const d = JSON.parse(text).data; n = Array.isArray(d) ? d.length : d ? 1 : 0; } catch {}
      this.countReads(bot, n);
      return new Response(text, { status: res.status, headers: { 'content-type': 'application/json', ...head } });
    }
    if (method === 'POST' && path === '/2/tweets') {
      const to = body?.reply?.in_reply_to_tweet_id;
      const allowed = new Set(['text', 'reply', 'media']);
      if (!ID.test(String(to || '')) || Object.keys(body || {}).some((k) => !allowed.has(k)) || String(body.text || '').length > 25000) {
        return json({ error: 'refused', message: 'summon only replies under its owner\'s posts' }, 403);
      }
      // Only under a post the owner wrote: no posting anywhere else on a trial key.
      let target = await this.x(bot, 'GET', `/2/tweets/${to}`, { 'post.fields': 'author_id' });
      if (target.status === 400) target = await this.x(bot, 'GET', `/2/tweets/${to}`, { 'tweet.fields': 'author_id' });
      const author = target.ok ? (await target.json())?.data?.author_id : null;
      this.countReads(bot, 1);
      if (!author && (target.status === 404 || target.ok)) {
        return json({ error: 'gone', message: 'the post that summoned the bot is gone (deleted or hidden), so nothing was posted' }, 404);
      }
      if (author !== bot.owner_id) return json({ error: 'refused', message: `summon only replies under @${bot.owner_handle}'s posts` }, 403);
      const res = await this.x(bot, 'POST', '/2/tweets', undefined, body);
      const text = await res.text();
      if (res.ok) {
        bot.free_left -= 1;
        this.sql.exec('UPDATE bots SET free_left = ? WHERE key_hash = ?', bot.free_left, bot.key_hash);
        this.setMeta('replies_given', (this.meta('replies_given') | 0) + 1);
      }
      return new Response(text, { status: res.status, headers: { 'content-type': 'application/json', 'x-summon-free-left': String(bot.free_left) } });
    }
    if (method === 'POST' && path === '/2/media/metadata') {
      const res = await this.x(bot, 'POST', path, undefined, body);
      return new Response(await res.text(), { status: res.status, headers: { 'content-type': 'application/json', ...head } });
    }
    return json({ error: 'refused', message: `summon doesn't call ${method} ${path}` }, 403);
  }

  async media(bot, req) {
    if (bot.free_left <= 0) return json({ error: 'trial_over' }, 402);
    const size = Number(req.headers.get('content-length') || 0);
    if (!size || size > MAX_MEDIA + 64 * 1024) return json({ error: 'refused', message: 'images up to 5 MB' }, 413);
    const res = await fetch(`${this.api}/2/media/upload`, {
      method: 'POST',
      headers: { authorization: `Bearer ${await this.access(bot)}`, 'content-type': req.headers.get('content-type') },
      body: req.body,
    });
    return new Response(await res.text(), { status: res.status, headers: { 'content-type': 'application/json' } });
  }
}
