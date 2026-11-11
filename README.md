# summon

Your own @grok on X that learns, run by your Hermes.

Tag your bot under any post and ask it something. Your Hermes reads the thread, its photos and the posts it quotes, then answers in the thread with your model, your memory and the tools you allow. Say "learn this" under a how-to thread and your agent keeps it as a skill, once you approve it.

Only you can summon it. A stranger tagging your bot never reaches your agent or your X bill.

## Set it up in two minutes

You need Hermes 0.23 or newer and a second X account for the bot.

1. Install the plugin.

   ```
   hermes plugins install summon
   ```

   Until it's in the catalog: `hermes plugins install https://github.com/gtmcknight/hermes-summon`, then `hermes plugins enable summon`.

2. Run setup. Enter your handle and pick the free trial, then open the link it prints on any device, signed in to X as the bot. No bot account yet? Pick "Borrow @taybotfyi" instead: your Hermes answers as @taybotfyi under your own posts, 10 replies, no sign-in.

   ```
   hermes gateway setup summon
   ```

3. Start the gateway, or restart it if it's already running.

   ```
   hermes gateway
   ```

4. Reply to any post with your bot's handle and a question.

The first 10 replies are free. After that, connect your own X app.

## What happens when you tag it

The bot checks X every 20 seconds for your posts that tag it, or that reply to it.

- It reads up to 6 replies above yours, the rest of the thread when the post is part of one, and any quoted posts.
- It sees up to 6 photos from them, and can watch their videos.
- It answers once, under your post, in one post when the bot has X Premium. Without it, a long answer goes out as numbered replies, up to 8.
- Images your agent makes are attached, up to 4.

To follow up, reply to the bot. Its replies are short and plain: the answer first, one source when it settles the question, never a recap. Hermes's `SOUL.md` sets its voice everywhere, X included.

## Teach it from X

Reply "learn this" under a thread that shows how to do something. Your Hermes writes it up as a skill and replies: Learned "skill-name". Approve it in Hermes to start using it.

The skill waits for you. In Hermes, run `/skills pending` to read it and approve it. From then on your agent uses it everywhere you talk to it, not just on X.

## Only you can summon it

- The bot asks X only for posts from your account. A stranger's post is never read and costs you nothing.
- Want a friend to summon it too? Add their handle to `X_BOT_ALLOWED_USERS` (your own X app only).
- The agent is told to treat the thread as something to read, never as instructions. Nothing typed on X can run a gateway command.
- A reply holding an API key, token or private key, or an email address or phone number that wasn't already in the thread, is held back instead of posted.

## Keep it to safe tools

Setup offers to limit X summons to web search, reading and making images, watching videos, and skills. Say yes. A post on X should never reach your terminal or your files.

It also turns on Hermes's skill approval, so nothing learned from X is used before you've read it. That covers skills from every chat, not just X. Change the tools later with `hermes tools`.

## What it costs

| | Free trial | Your own X app |
| --- | --- | --- |
| Replies | First 10 free | About $0.01 each, paid to X |
| Reading the thread | Free | About $0.005 a post |
| Limit | 300 posts read a day | $1.00 a day, set by `X_BOT_DAILY_BUDGET` |

## After the trial, use your own X app

1. Sign in to [console.x.com](https://console.x.com) as the bot and make an app in a Pay Per Use project.
2. Set its permissions to Read and write, then generate the access token. A token made before that change can only read.
3. Run `hermes gateway setup summon`, pick "My own X app" and paste the four values from Keys and tokens.

Setup asks X which account the keys post as, and refuses keys for your own account.

## Settings

| Variable | What it does | Default |
| --- | --- | --- |
| `X_BOT_OWNER` | Your handle. Only you can summon the bot | Required |
| `X_BOT_ALLOWED_USERS` | More handles that can summon it, comma-separated (own X app only) | None |
| `X_BOT_POLL_SECONDS` | Seconds between checks, 15 at least | 20 |
| `X_BOT_POST_LIMIT` | Characters per post. Leave it empty and the bot finds out: one long post with X Premium, 280 after X refuses one | Learned |
| `X_BOT_DAILY_BUDGET` | Most the bot spends on X in a day, in dollars (own X app) | 1.00 |
| `X_BOT_DRY_RUN` | `true` writes replies to the log instead of posting them | false |
| `X_BOT_TIMEZONE` | Time zone for the dates the agent sees | This machine's |
| `X_BOT_RELAY_URL` | The free trial relay | https://summon.taylor.fyi |

Setup writes the keys: `X_BOT_RELAY_KEY` for the trial, or `X_BOT_API_KEY`, `X_BOT_API_SECRET`, `X_BOT_ACCESS_TOKEN` and `X_BOT_ACCESS_SECRET` for your own app.

## What it touches

- **Network.** The bot calls the X API (api.x.com) to find your summons, read threads and post replies, and downloads photos from X's image servers. To watch a video, Hermes sends X's smallest copy of it to the video model Hermes is set up with. On the free trial, the X calls go through summon.taylor.fyi instead.
- **Background work.** While the gateway runs, the bot checks X every 20 seconds. Nothing runs when the gateway is stopped.
- **Credentials.** Setup saves your X keys or the trial key to Hermes's `.env` through Hermes. The plugin reads them only through Hermes's secret scope, and never runs an X sign-in itself.
- **Settings.** With your OK, setup sets `platform_toolsets.summon` to `[safe, video, skills]` and turns on `skills.write_approval` for all of Hermes.
- **Files.** In its own data folder it keeps `state.json` (the last post seen, the posts it answered, each day's spend) and `summons.jsonl` (each summons: the post link, who asked, the question, what happened). Photos go to Hermes's image cache.
- **Nothing else.** No shell commands and no telemetry.

## The free trial relay

summon.taylor.fyi is a Cloudflare Worker run by the plugin's author. Its code is in [`relay/`](relay/).

- When you sign in as the bot, the relay keeps the bot's X sign-in and gives the plugin a key. The relay stores only a hash of that key.
- "Borrow @taybotfyi" gives you a key for the author's own bot instead, with no sign-in. It can only reply under your posts, 10 times, and the author can switch it off.
- It forwards only what summon needs: the search for your summons, thread reads, and replies under posts you wrote. Everything else is refused, including other searches, posts that aren't replies to your posts, DMs, likes and follows.
- It stores your handle and the bot's, both user IDs, the bot's X tokens, and counts of replies and reads. It doesn't store posts, and it logs errors only.
- To cut it off, revoke the app in the bot's X settings under Connected apps.

To run your own, deploy `relay/` with your own X app and set `X_BOT_RELAY_URL`.

## Run the tests

```
python3 -m unittest discover -s tests
relay/test/run-e2e.sh
```

The second runs the relay against a fake X, and needs wrangler.
