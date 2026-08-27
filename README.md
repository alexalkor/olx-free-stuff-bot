# OLX Free Stuff Bot

Scrapes free ("za darmo") listings from OLX and stores them as a GitHub-backed
JSON snapshot. Sibling of
[telegram-multilang-bot](https://github.com/alexalkor/telegram-multilang-bot)
(the Warsaw events bot).

## Status (as of 2026-08-27)

**Railway has been decommissioned.** The Python/aiogram Telegram bot in this
repo (`main.py` and its supporting `config/`, `database/`, `handlers/`,
`keyboards/`, `locales/`, `utils/` modules) is **legacy code and is not
currently deployed anywhere**. It's kept for reference only — see the notice
at the top of `main.py`, and the **Legacy** section below.

The project now runs entirely on **Git + Cloudflare**, nothing else:

```
OLX Free Stuff Scrapper          Cloudflare Worker                 GitHub
  (local scheduled task)  --POST-->  olx-free-stuff-worker  --PUT-->  this repo
                          (auth: X-Secret header)    |            (data/offers.json)
                                                      +-> Workers KV
                                                          (OFFERS_KV, key "latest")
```

1. The scraper (a local scheduled task, not part of this repo) scrapes OLX,
   filters for free listings, and `POST`s them to the Cloudflare Worker
   `olx-free-stuff-worker`.
2. The Worker stores the payload in Workers KV under key `"latest"`, then
   backs it up to this repo as `data/offers.json` via the GitHub Contents API.
3. **There is currently no live Telegram bot reading this data.** The
   `GET /offers` endpoint on the Worker exists for debugging (and as a hook
   for a future bot), but nothing today pushes these listings to Telegram
   users. If the bot shows nothing, that's expected — no service currently
   sends Telegram messages.

The Worker's source is **not** checked into this repo — it's edited directly
in the Cloudflare dashboard's Quick Editor. A mirror of the deployed source
is kept here for version control: [`cloudflare/olx-free-stuff-worker.js`](cloudflare/olx-free-stuff-worker.js)
(update it manually after dashboard edits — it isn't auto-synced).

## Worker endpoints (`olx-free-stuff-worker`, Cloudflare)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Liveness check |
| `GET` | `/offers` | Returns the last-stored payload from KV (debugging / future bot) |
| `POST` | `/offers` | Body: `{"listings":[{title,link,description,posted_at}, ...], "date":"YYYY-MM-DD"}`, header `X-Secret: <WEBHOOK_SECRET>`. Stores to KV, then backs up to GitHub as `data/offers.json`. |

Required Cloudflare configuration (Settings → Variables/Bindings on
`olx-free-stuff-worker`):

- KV namespace binding `OFFERS_KV`
- `GITHUB_REPO` = `alexalkor/olx-free-stuff-bot` (plain text var)
- `GITHUB_FILE` = `data/offers.json` (plain text var)
- `WEBHOOK_SECRET` — must match the `X-Secret` header the scraper sends (secret)
- `GITHUB_PAT` — fine-grained PAT scoped to this repo, `Contents: Read and write` (secret)

No cron trigger is configured on the Worker — it only runs when the scraper's
scheduled task POSTs to it.

## `data/offers.json` format (current, live)

This file mirrors whatever the Worker stores in KV. It is **not** the old
`{"raw": ..., "translations": {...}}` shape the legacy bot code (below)
expects — anything that reads this file today needs to expect this shape
instead:

```json
{
  "date": "2026-08-27",
  "listings": [
    {
      "title": "...",
      "link": "https://www.olx.pl/d/oferta/...",
      "description": "...",
      "posted_at": "Dzisiaj o 16:27"
    }
  ],
  "timestamp": "2026-08-27T14:49:12.000Z"
}
```

## Repository layout

```
.
├── cloudflare/
│   └── olx-free-stuff-worker.js   # mirror of the deployed Worker — see Status above
├── main.py                        # LEGACY — aiohttp + aiogram polling bot, not deployed
├── .env.example                   # LEGACY — only relevant if main.py is redeployed somewhere
├── requirements.txt                # LEGACY
├── config/
│   └── settings.py                # LEGACY
├── database/
│   ├── db.py                      # LEGACY — SQLite: users, offers, offer_translations
│   └── github_storage.py          # LEGACY — expects the old {"raw","translations"} JSON shape
├── handlers/                      # LEGACY — /start, /help, /language, menu, admin
├── keyboards/                     # LEGACY
├── locales/                       # LEGACY — en/pl/ru/be/uk/de JSON
├── utils/                         # LEGACY — i18n loader, MyMemory translator
└── data/
    └── offers.json                # LIVE — written by the Cloudflare Worker, see format above
```

## Legacy: the Railway/aiogram bot (not deployed)

Everything in this section describes the bot as it worked **before Railway
was decommissioned**. None of it runs today. It's left in the repo in case
Telegram delivery gets rebuilt later — most likely as a Cloudflare Worker
acting as a Telegram webhook (the pattern `warsaw-events-worker` now uses
for the sibling project), rather than a redeployed Railway/aiogram polling
process.

<details>
<summary>Expand for the original Railway-era documentation</summary>

A Telegram bot built with Python and aiogram 3.x that posted free ("za
darmo") listings scraped from OLX. It supported 6 languages: Polish,
English, Russian, Belarusian, Ukrainian, and German.

### How it fit together (Railway era)

```
OLX Free Stuff Scrapper    this bot (Railway)      Telegram users
  (scheduled task)  --POST-->  /offers webhook  --menu-->  see the offers
                     (auth: X-Secret header)
```

1. The scheduled scraper task scraped OLX, filtered for free listings, and
   formatted them as a numbered, newline-separated list (see **Offer
   format** below).
2. It `POST`ed that text to the bot's `/offers` endpoint.
3. The bot stored it (SQLite + a GitHub-backed JSON backup so it survived
   redeploys), machine-translated it into the other 5 languages in the
   background, and served it to any user who tapped the **Free OLX offers**
   button in the menu.

### Setup

1. **Prerequisites**: Python 3.10+
2. **Install dependencies**: `pip install -r requirements.txt`
3. **Configure environment**: `cp .env.example .env`, then fill in:

   | Variable | Purpose |
   |-------------------|--------------------------------------------------------------------------|
   | `BOT_TOKEN` | Telegram bot token from @BotFather (for `@olxfreestuffbot`) |
   | `WEBHOOK_SECRET` | Shared secret the scraper's scheduled task must send as `X-Secret` |
   | `PORT` | HTTP port for the aiohttp server (Railway set this automatically) |
   | `ADMIN_USER_ID` | Telegram numeric user ID (send `/myid` to the bot to find it) |
   | `GITHUB_PAT` | A GitHub PAT with `contents:write` on this repo, for the JSON backup |
   | `MYMEMORY_EMAIL` | Email registered with MyMemory for the higher 50k-word/day quota |
   | `GITHUB_REPO` | `owner/repo` — defaults to `alexalkor/olx-free-stuff-bot` |

4. **Run the bot**: `python main.py` — polls Telegram for updates and also
   runs a small HTTP server (for the scraper webhook + health checks).

### Commands

| Command | Description |
|--------------|--------------------------------------------------|
| `/start` | Shows the language picker (new users) or menu |
| `/language` | Shows the language picker again |
| `/help` | Displays help text in the user's chosen language |
| `/myid` | Replies with your Telegram user ID |

### Menu

- **🆓 Free OLX offers** — shows the current batch of free listings
- **🌐 Change language** — re-opens the language picker
- **⛔ Stop** — stops the bot (send `/start` to resume)

### Feeding it from the scheduled task (Railway era)

`POST /offers` with header `X-Secret: <WEBHOOK_SECRET>` and JSON body:

```json
{
  "text": "1. 🆓 Kanapa rozkładana, stan dobry\nOddam za darmo, trzeba odebrać.\n📍 Warszawa, Mokotów\n🔗 https://www.olx.pl/d/oferta/...\n\n2. 🆓 Zabawki dla dzieci\n...",
  "mode": "replace"
}
```

- `mode: "replace"` (default) — wipes the current batch and cached
  translations, then stores this text as the new one.
- `mode: "append"` — adds these listings onto the current batch instead of
  replacing it.

#### Offer format

Each offer was a numbered item (`N. `) so the bot could split long batches
across multiple Telegram messages without breaking one listing apart:

```
N. 🆓 <title>
<optional description line(s)>
📍 <location>
🔗 <OLX link>
```

Prefixing the title with the placeholder emoji `🆓` let the bot swap in a
more specific category emoji based on Polish keywords in the title — see
`_assign_emojis` in `main.py`.

Other legacy endpoints: `GET /debug` (version, cached-translation counts,
whether `GITHUB_PAT` is set), `POST /admin/clear-cache` (wipes cached
translations, same `X-Secret` auth), `GET /test-translate` (sanity-checks
the MyMemory integration).

### How it worked

- Offer text was stored per-batch in SQLite (`bot.db`) and mirrored to
  `data/offers.json` via the GitHub Contents API, so a Railway redeploy
  didn't lose the current listings.
- Translations (Polish → English/Russian/Belarusian/Ukrainian/German) were
  computed lazily via the free MyMemory API, cached in SQLite + the GitHub
  JSON backup.
- Language preference was stored per user in SQLite.
- `/myid` + `ADMIN_USER_ID` gave a manual way to push a batch of offers from
  Telegram itself.

</details>
