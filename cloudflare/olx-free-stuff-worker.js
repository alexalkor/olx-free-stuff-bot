// OLX Free Stuff Worker — Cloudflare Worker
// Receives the daily scraped listings from the "olx-free-warsaw-stuff-scrapper"
// scheduled task, stores them in KV, backs them up to GitHub, and now also
// serves them to Telegram users via an inline-keyboard menu (same pattern as
// the sibling warsaw-events-worker / telegram-multilang-bot project: a
// webhook receiving Telegram updates, a menu button + a change-language
// button, per-chat language stored in KV). Content itself is NOT translated
// (same as before) — only the menu/help chrome is localized.
//
// Required setup in the Cloudflare dashboard for this Worker:
//   Bindings:
//     - KV namespace binding named OFFERS_KV (already exists — also stores
//       each chat's language preference under key "lang:<chatId>")
//   Variables (Settings > Variables):
//     - GITHUB_REPO = "alexalkor/olx-free-stuff-bot"        (plain text var)
//     - GITHUB_FILE = "data/offers.json"                     (plain text var)
//   Secrets (Settings > Variables > "Encrypt" / Secrets):
//     - WEBHOOK_SECRET = <shared secret>. Used three ways:
//         1. X-Secret header the scraper sends on POST /offers (unchanged)
//         2. the Telegram "secret_token" the webhook request must carry
//            (Telegram echoes it back as header X-Telegram-Bot-Api-Secret-Token)
//         3. the ?secret= query param that gates the /admin/* endpoints below
//     - GITHUB_PAT      = <GitHub PAT, fine-grained, Contents: Read/write,
//                          scoped to only olx-free-stuff-bot>
//     - BOT_TOKEN       = <Telegram bot token from @BotFather for the OLX
//                          bot. NEW — required for any Telegram functionality.
//                          Nothing works without this being set.>
//
// One-time setup after deploying this file with BOT_TOKEN + WEBHOOK_SECRET set:
//   Visit  https://<your-worker-subdomain>/admin/set-webhook?secret=<WEBHOOK_SECRET>
//   once in a browser. That registers this Worker's /webhook URL with
//   Telegram (using WEBHOOK_SECRET as the secret_token). You can re-visit
//   /admin/webhook-info?secret=<WEBHOOK_SECRET> any time to check status.
//
// Endpoints:
//   GET  /health              -> liveness check
//   GET  /offers               -> last-stored payload from KV (debugging)
//   POST /offers               -> scraper ingest, unchanged (see below)
//   POST /webhook               -> Telegram update delivery (set via /admin/set-webhook)
//   GET  /admin/set-webhook     -> (auth: ?secret=) registers the Telegram webhook
//   GET  /admin/webhook-info    -> (auth: ?secret=) proxies Telegram getWebhookInfo
//
// NOTE: this file is a manually-maintained mirror of the Worker as edited in
// the Cloudflare dashboard's Quick Editor. The dashboard copy is the source
// of truth — update this file by hand after making changes there, it is not
// auto-synced.

const VERSION = "v2.0-olx-worker-telegram";
const TG_MAX_CHARS = 3500; // stay comfortably under Telegram's 4096 limit

function json(data, status) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function toBase64Utf8(str) {
  const bytes = new TextEncoder().encode(str);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function githubBackup(env, contentObj) {
  const repo = env.GITHUB_REPO || "alexalkor/olx-free-stuff-bot";
  const path = env.GITHUB_FILE || "data/offers.json";
  const apiUrl = `https://api.github.com/repos/${repo}/contents/${path}`;
  const headers = {
    "Authorization": `Bearer ${env.GITHUB_PAT}`,
    "Accept": "application/vnd.github+json",
    "User-Agent": "olx-free-stuff-worker",
  };

  let sha;
  const getResp = await fetch(apiUrl, { headers });
  if (getResp.status === 200) {
    const cur = await getResp.json();
    sha = cur.sha;
  } else if (getResp.status !== 404) {
    const errText = await getResp.text();
    return { status: getResp.status, message: `GitHub GET failed: ${errText.slice(0, 200)}` };
  }

  const body = {
    message: `Update OLX offers — ${new Date().toISOString().slice(0, 10)}`,
    content: toBase64Utf8(JSON.stringify(contentObj, null, 2)),
    ...(sha ? { sha } : {}),
  };
  const putResp = await fetch(apiUrl, {
    method: "PUT",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const putJson = await putResp.json().catch(() => ({}));
  if (putResp.status === 200 || putResp.status === 201) {
    return { status: putResp.status, message: "ok", commit: putJson.commit?.sha };
  }
  return { status: putResp.status, message: `GitHub PUT failed: ${JSON.stringify(putJson).slice(0, 200)}` };
}

// ---------------------------------------------------------------------------
// Telegram: i18n, keyboards, API helpers
// ---------------------------------------------------------------------------

const LANG_NAMES = { en: "English", pl: "Polski", ru: "Русский", be: "Беларуская", uk: "Українська", de: "Deutsch" };

const I18N = {
  en: {
    welcome: "👋 Welcome! I post free (\"za darmo\") listings scraped from OLX Warsaw.\n\nChoose your language:",
    lang_set: (name) => `✅ Language set to ${name}.`,
    choose_action: "What would you like to do?",
    btn_offers: "🆓 Free OLX offers",
    btn_lang: "🌐 Change language",
    btn_stop: "⛔ Stop",
    stopped: "Stopped. Send /start any time to resume.",
    no_offers: "No offers stored yet — check back after the next scrape.",
    header: "🆓 Free stuff on OLX Warsaw:",
    footer: "— That's the current batch. Use the menu below to check again later.",
    help: "/start — show the menu\n/language — change language\n/help — this message",
  },
  pl: {
    welcome: "👋 Witaj! Publikuję darmowe ogłoszenia (\"za darmo\") z OLX Warszawa.\n\nWybierz język:",
    lang_set: (name) => `✅ Ustawiono język: ${name}.`,
    choose_action: "Co chcesz zrobić?",
    btn_offers: "🆓 Darmowe ogłoszenia OLX",
    btn_lang: "🌐 Zmień język",
    btn_stop: "⛔ Stop",
    stopped: "Zatrzymano. Wyślij /start, aby wznowić.",
    no_offers: "Brak zapisanych ogłoszeń — sprawdź ponownie po następnym skanowaniu.",
    header: "🆓 Darmowe rzeczy na OLX Warszawa:",
    footer: "— To bieżąca partia. Użyj menu poniżej, aby sprawdzić ponownie później.",
    help: "/start — pokaż menu\n/language — zmień język\n/help — ta wiadomość",
  },
  ru: {
    welcome: "👋 Привет! Публикую бесплатные (\"za darmo\") объявления с OLX Варшава.\n\nВыберите язык:",
    lang_set: (name) => `✅ Язык установлен: ${name}.`,
    choose_action: "Что вы хотите сделать?",
    btn_offers: "🆓 Бесплатные объявления OLX",
    btn_lang: "🌐 Сменить язык",
    btn_stop: "⛔ Стоп",
    stopped: "Остановлено. Отправьте /start, чтобы продолжить.",
    no_offers: "Пока нет сохранённых объявлений — проверьте позже, после следующего сканирования.",
    header: "🆓 Бесплатные вещи на OLX Варшава:",
    footer: "— Это текущая партия. Используйте меню ниже, чтобы проверить позже.",
    help: "/start — показать меню\n/language — сменить язык\n/help — это сообщение",
  },
  be: {
    welcome: "👋 Прывітанне! Публікую бясплатныя (\"za darmo\") аб'явы з OLX Варшава.\n\nАбярыце мову:",
    lang_set: (name) => `✅ Мова ўстаноўлена: ${name}.`,
    choose_action: "Што вы хочаце зрабіць?",
    btn_offers: "🆓 Бясплатныя аб'явы OLX",
    btn_lang: "🌐 Змяніць мову",
    btn_stop: "⛔ Стоп",
    stopped: "Спынена. Дашліце /start, каб аднавіць.",
    no_offers: "Пакуль няма захаваных аб'яў — праверце пазней, пасля наступнага скану.",
    header: "🆓 Бясплатныя рэчы на OLX Варшава:",
    footer: "— Гэта бягучая партыя. Скарыстайцеся меню ніжэй, каб праверыць пазней.",
    help: "/start — паказаць меню\n/language — змяніць мову\n/help — гэта паведамленне",
  },
  uk: {
    welcome: "👋 Привіт! Публікую безкоштовні (\"za darmo\") оголошення з OLX Варшава.\n\nОберіть мову:",
    lang_set: (name) => `✅ Мову встановлено: ${name}.`,
    choose_action: "Що ви хочете зробити?",
    btn_offers: "🆓 Безкоштовні оголошення OLX",
    btn_lang: "🌐 Змінити мову",
    btn_stop: "⛔ Стоп",
    stopped: "Зупинено. Надішліть /start, щоб відновити.",
    no_offers: "Поки що немає збережених оголошень — перевірте пізніше, після наступного сканування.",
    header: "🆓 Безкоштовні речі на OLX Варшава:",
    footer: "— Це поточна партія. Скористайтеся меню нижче, щоб перевірити пізніше.",
    help: "/start — показати меню\n/language — змінити мову\n/help — це повідомлення",
  },
  de: {
    welcome: "👋 Willkommen! Ich poste kostenlose (\"za darmo\") Anzeigen von OLX Warschau.\n\nSprache wählen:",
    lang_set: (name) => `✅ Sprache eingestellt: ${name}.`,
    choose_action: "Was möchtest du tun?",
    btn_offers: "🆓 Kostenlose OLX-Anzeigen",
    btn_lang: "🌐 Sprache ändern",
    btn_stop: "⛔ Stop",
    stopped: "Gestoppt. Sende /start, um fortzufahren.",
    no_offers: "Noch keine Anzeigen gespeichert — später nach dem nächsten Scan erneut prüfen.",
    header: "🆓 Kostenlose Sachen auf OLX Warschau:",
    footer: "— Das ist die aktuelle Charge. Nutze das Menü unten, um später erneut zu prüfen.",
    help: "/start — Menü anzeigen\n/language — Sprache ändern\n/help — diese Nachricht",
  },
};

function tr(lang, key, ...args) {
  const table = I18N[lang] || I18N.en;
  const val = table[key] ?? I18N.en[key];
  return typeof val === "function" ? val(...args) : val;
}

function langKeyboard() {
  const codes = Object.keys(LANG_NAMES);
  const rows = [];
  for (let i = 0; i < codes.length; i += 2) {
    rows.push(codes.slice(i, i + 2).map((c) => ({ text: LANG_NAMES[c], callback_data: `lang:${c}` })));
  }
  return { inline_keyboard: rows };
}

function menuKeyboard(lang) {
  return {
    inline_keyboard: [
      [{ text: tr(lang, "btn_offers"), callback_data: "menu:offers" }],
      [{ text: tr(lang, "btn_lang"), callback_data: "menu:lang" }],
      [{ text: tr(lang, "btn_stop"), callback_data: "menu:stop" }],
    ],
  };
}

async function tg(env, method, body) {
  const resp = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return resp.json().catch(() => ({}));
}

function send(env, chatId, text, extra = {}) {
  return tg(env, "sendMessage", { chat_id: chatId, text, parse_mode: "HTML", ...extra });
}

function ack(env, callbackQueryId, text) {
  return tg(env, "answerCallbackQuery", { callback_query_id: callbackQueryId, ...(text ? { text } : {}) });
}

async function getUserLang(kv, chatId) {
  return (await kv.get(`lang:${chatId}`)) || "en";
}

// ---------------------------------------------------------------------------
// Offers formatting / chunking
// ---------------------------------------------------------------------------

function formatListing(listing, idx) {
  const lines = [`${idx}. 🆓 ${escapeHtml(listing.title || "")}`];
  if (listing.description) lines.push(escapeHtml(listing.description));
  if (listing.posted_at) lines.push(`🕒 ${escapeHtml(listing.posted_at)}`);
  if (listing.link) lines.push(listing.link);
  return lines.join("\n");
}

function buildOfferBlocks(stored, lang) {
  const header = tr(lang, "header") + (stored.date ? `\n<i>${escapeHtml(stored.date)}</i>` : "");
  const items = (stored.listings || []).map((l, i) => formatListing(l, i + 1));
  const footer = tr(lang, "footer");
  return [header, ...items, footer];
}

function chunkBlocks(blocks, maxChars) {
  const chunks = [];
  let cur = "";
  for (const block of blocks) {
    const next = cur ? `${cur}\n\n${block}` : block;
    if (next.length > maxChars && cur) {
      chunks.push(cur);
      cur = block;
    } else {
      cur = next;
    }
  }
  if (cur) chunks.push(cur);
  return chunks;
}

async function sendOffers(env, chatId, lang) {
  const stored = await env.OFFERS_KV.get("latest", { type: "json" });
  if (!stored || !Array.isArray(stored.listings) || stored.listings.length === 0) {
    await send(env, chatId, tr(lang, "no_offers"), { reply_markup: menuKeyboard(lang) });
    return;
  }
  const blocks = buildOfferBlocks(stored, lang);
  const chunks = chunkBlocks(blocks, TG_MAX_CHARS);
  for (let i = 0; i < chunks.length; i++) {
    const last = i === chunks.length - 1;
    await send(env, chatId, chunks[i], last ? { reply_markup: menuKeyboard(lang) } : {});
  }
}

// ---------------------------------------------------------------------------
// Update routing (mirrors warsaw-events-worker's handleUpdate pattern)
// ---------------------------------------------------------------------------

async function handleUpdate(update, env) {
  const kv = env.OFFERS_KV;

  if (update.message) {
    const { chat, text = "" } = update.message;
    const chatId = chat.id;
    const lang = await getUserLang(kv, chatId);
    const cmd = text.split(" ")[0].split("@")[0];

    if (cmd === "/start") await send(env, chatId, tr(lang, "welcome"), { reply_markup: langKeyboard() });
    else if (cmd === "/language") await send(env, chatId, "🌐", { reply_markup: langKeyboard() });
    else if (cmd === "/help") await send(env, chatId, tr(lang, "help"));
    return;
  }

  if (update.callback_query) {
    const cb = update.callback_query;
    const chatId = cb.message.chat.id;
    const data = cb.data || "";
    await ack(env, cb.id);
    let lang = await getUserLang(kv, chatId);

    if (data.startsWith("lang:")) {
      lang = data.split(":")[1];
      await kv.put(`lang:${chatId}`, lang);
      await send(env, chatId, tr(lang, "lang_set", LANG_NAMES[lang] || lang), { reply_markup: menuKeyboard(lang) });
      return;
    }
    if (data === "menu:lang") {
      await send(env, chatId, "🌐", { reply_markup: langKeyboard() });
      return;
    }
    if (data === "menu:stop") {
      await send(env, chatId, tr(lang, "stopped"));
      return;
    }
    if (data === "menu:offers") {
      await sendOffers(env, chatId, lang);
      return;
    }
  }
}

// ---------------------------------------------------------------------------
// Admin helpers (webhook registration) — gated by WEBHOOK_SECRET
// ---------------------------------------------------------------------------

async function tgRaw(env, method, params) {
  const u = new URL(`https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`);
  for (const [k, v] of Object.entries(params || {})) u.searchParams.set(k, v);
  const resp = await fetch(u.toString());
  return resp.json().catch(() => ({}));
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/health") {
      return json({ ok: true, version: VERSION });
    }

    if (url.pathname === "/offers" && request.method === "GET") {
      const stored = await env.OFFERS_KV.get("latest", { type: "json" });
      return json({ ok: true, data: stored || null });
    }

    if (url.pathname === "/offers" && request.method === "POST") {
      const secret = request.headers.get("X-Secret") || "";
      if (!env.WEBHOOK_SECRET || secret !== env.WEBHOOK_SECRET) {
        return json({ ok: false, error: "Unauthorized" }, 401);
      }

      let payload;
      try {
        payload = await request.json();
      } catch (e) {
        return json({ ok: false, error: "Invalid JSON body" }, 400);
      }

      const listings = Array.isArray(payload.listings) ? payload.listings : null;
      if (!listings) {
        return json({ ok: false, error: "Expected { listings: [...] }" }, 400);
      }

      const stored = {
        date: payload.date || new Date().toISOString().slice(0, 10),
        listings,
        timestamp: new Date().toISOString(),
      };

      await env.OFFERS_KV.put("latest", JSON.stringify(stored));

      let github = { status: 0, message: "skipped (no GITHUB_PAT configured)" };
      if (env.GITHUB_PAT) {
        try {
          github = await githubBackup(env, stored);
        } catch (e) {
          github = { status: 0, message: `GitHub backup threw: ${e.message}` };
        }
      }

      return json({ ok: true, count: listings.length, github });
    }

    if (url.pathname === "/webhook" && request.method === "POST") {
      if (!env.WEBHOOK_SECRET || request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
        return json({ ok: false, error: "Unauthorized" }, 401);
      }
      if (!env.BOT_TOKEN) {
        return json({ ok: false, error: "BOT_TOKEN not configured" }, 500);
      }
      let update;
      try {
        update = await request.json();
      } catch (e) {
        return json({ ok: false, error: "Invalid JSON body" }, 400);
      }
      ctx.waitUntil(handleUpdate(update, env).catch((e) => console.error("handleUpdate:", e)));
      return json({ ok: true });
    }

    if (url.pathname === "/admin/set-webhook" && request.method === "GET") {
      if (!env.WEBHOOK_SECRET || url.searchParams.get("secret") !== env.WEBHOOK_SECRET) {
        return json({ ok: false, error: "Unauthorized" }, 401);
      }
      if (!env.BOT_TOKEN) return json({ ok: false, error: "BOT_TOKEN not configured" }, 500);
      const webhookUrl = `${url.origin}/webhook`;
      const result = await tgRaw(env, "setWebhook", { url: webhookUrl, secret_token: env.WEBHOOK_SECRET });
      return json({ ok: true, webhookUrl, telegram: result });
    }

    if (url.pathname === "/admin/webhook-info" && request.method === "GET") {
      if (!env.WEBHOOK_SECRET || url.searchParams.get("secret") !== env.WEBHOOK_SECRET) {
        return json({ ok: false, error: "Unauthorized" }, 401);
      }
      if (!env.BOT_TOKEN) return json({ ok: false, error: "BOT_TOKEN not configured" }, 500);
      const result = await tgRaw(env, "getWebhookInfo", {});
      return json({ ok: true, telegram: result });
    }

    return json({ ok: false, error: "not found" }, 404);
  },
};
