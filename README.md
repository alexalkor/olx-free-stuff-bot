# OLX Free Stuff Bot

A Telegram bot built with Python and aiogram 3.x that posts free ("za darmo")
listings scraped from OLX. It supports 6 languages: Polish, English, Russian,
Belarusian, Ukrainian, and German. It is a sibling of
[telegram-multilang-bot](https://github.com/alexalkor/telegram-multilang-bot)
(the Warsaw events bot) — same menu, same architecture, same deploy story —
just fed by the **OLX Free Stuff Scrapper** scheduled task instead of a
Telegram channel.

## How it fits together

```
OLX Free Stuff Scrapper          this bot (Railway)              Telegram users
 (scheduled task)      --POST-->   /offers webhook     --menu-->   see the offers
                                  (auth: X-Secret header)
```

1. The scheduled scraper task scrapes OLX, filters for free listings, and
   formats them as a numbered, newline-separated list (see **Offer format**
   below).
2. It `POST`s that text to this bot's `/offers` endpoint.
3. The bot stores it (SQLite + a GitHub-backed JSON backup so it survives
   redeploys), machine-translates it into the other 5 languages in the
   background, and serves it to any user who taps the **Free OLX offers**
   button in the menu.

## Project structure

```
.
├── main.py                    # aiohttp server + aiogram polling bot
├── .env.example                # copy to .env and fill in
├── requirements.txt
├── config/
│   └── settings.py            # loads all env vars
├── database/
│   ├── db.py                  # SQLite: users, offers, offer_translations
│   └── github_storage.py      # backs up data/offers.json to this repo via the GitHub API
├── handlers/
│   ├── start.py                # /start
│   ├── help.py                 # /help
│   ├── language.py             # /language + language picker callback
│   ├── menu.py                  # menu:offers / menu:change_lang / menu:stop
│   └── admin.py                 # manual fallback: any text from ADMIN_USER_ID replaces the current offers
├── keyboards/
│   ├── language_kb.py
│   └── menu_kb.py
├── locales/                    # en.json, pl.json, ru.json, be.json, uk.json, de.json
├── utils/
│   ├── i18n.py                  # locale loader
│   └── translator.py            # MyMemory API, source language = Polish
└── data/
    └── offers.json               # {"raw": ..., "translations": {...}} — kept in sync by the bot itself
```

## Setup

### 1. Prerequisites
Python 3.10+

### 2. Install dependencies
```
pip install -r requirements.txt
```

### 3. Configure environment
```
cp .env.example .env
```
Then fill in `.env`:

| Variable          | Purpose                                                                 |
|-------------------|--------------------------------------------------------------------------|
| `BOT_TOKEN`       | Telegram bot token from @BotFather (for `@olxfreestuffbot`)             |
| `WEBHOOK_SECRET`  | Shared secret the scraper's scheduled task must send as `X-Secret`      |
| `PORT`            | HTTP port for the aiohttp server (Railway sets this automatically)      |
| `ADMIN_USER_ID`   | Your Telegram numeric user ID (send `/myid` to the bot to find it)      |
| `GITHUB_PAT`      | A GitHub PAT with `contents:write` on this repo, for the JSON backup    |
| `MYMEMORY_EMAIL`  | Email registered with MyMemory for the higher 50k-word/day quota        |
| `GITHUB_REPO`     | `owner/repo` — defaults to `alexalkor/olx-free-stuff-bot`               |

### 4. Run the bot
```
python main.py
```
The bot polls Telegram for updates and also runs a small HTTP server (for
the scraper webhook + health checks). Press Ctrl+C to stop.

## Commands

| Command      | Description                                    |
|--------------|--------------------------------------------------|
| `/start`     | Shows the language picker (new users) or menu    |
| `/language`  | Shows the language picker again                  |
| `/help`      | Displays help text in the user's chosen language  |
| `/myid`      | Replies with your Telegram user ID                |

## Menu

Same three buttons as the baseline events bot, just repointed at offers:

- **🆓 Free OLX offers** — shows the current batch of free listings
- **🌐 Change language** — re-opens the language picker
- **⛔ Stop** — stops the bot (send `/start` to resume)

## Feeding it from the scheduled task

`POST /offers` with header `X-Secret: <WEBHOOK_SECRET>` and JSON body:

```json
{
  "text": "1. 🆓 Kanapa rozkładana, stan dobry\nOddam za darmo, trzeba odebrać.\n📍 Warszawa, Mokotów\n🔗 https://www.olx.pl/d/oferta/...\n\n2. 🆓 Zabawki dla dzieci\n...",
  "mode": "replace"
}
```

- `mode: "replace"` (default) — wipes the current batch and cached
  translations, then stores this text as the new one. Use this for a full
  fresh scrape.
- `mode: "append"` — adds these listings onto the current batch instead of
  replacing it (translations are re-computed since the text changed).

### Offer format

Each offer is a numbered item (`N. `) so the bot can split long batches
across multiple Telegram messages without breaking one listing apart. A
suggested per-item shape (mirrors how the baseline bot's events look):

```
N. 🆓 <title>
<optional description line(s)>
📍 <location>
🔗 <OLX link>
```

Prefixing the title with the placeholder emoji `🆓` lets the bot swap in a
more specific category emoji (🛋 furniture, 💻 electronics, 👕 clothes, 🧸
toys, 📚 books, 🪴 plants, 🧰 tools/building materials, 🍼 baby gear, 🍽
kitchenware, …) based on Polish keywords in the title — see
`_assign_emojis` in `main.py`. Anything that doesn't match a rule keeps 🆓.

Other endpoints:

- `GET /health` — liveness check
- `GET /debug` — version, cached-translation counts, whether `GITHUB_PAT` is set
- `POST /admin/clear-cache` — wipes cached translations (same `X-Secret` auth)
- `GET /test-translate` — sanity-checks the MyMemory integration against real DB content

## How it works

- Offer text is stored per-batch in SQLite (`bot.db`, auto-created on first
  run) and mirrored to `data/offers.json` in this repo via the GitHub
  Contents API, so a Railway redeploy doesn't lose the current listings.
- Translations (Polish → English/Russian/Belarusian/Ukrainian/German) are
  computed lazily via the free MyMemory API, cached in SQLite + the GitHub
  JSON backup, and re-used until the source text changes.
- Language preference is stored per user in SQLite. Unknown users default to
  English until they pick a language.
- `/myid` + `ADMIN_USER_ID` give you a manual way to push a batch of offers
  from Telegram itself, without waiting on the scheduled scraper — handy for
  testing.

## Deployment

Deployed the same way as the baseline bot: push to `main` on Railway (or any
host that runs `python main.py` with the env vars above set), with
`GITHUB_PAT` scoped to this repo so the bot can commit `data/offers.json`
updates itself. Commits made by the bot use `[skip railway]` in the message
so they don't themselves trigger a redeploy loop — configure your Railway
deploy trigger to respect that if it doesn't already.
