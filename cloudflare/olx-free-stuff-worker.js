// OLX Free Stuff Worker — Cloudflare Worker
// Receives the daily scraped listings from the "olx-free-warsaw-stuff-scrapper"
// scheduled task and backs them up to GitHub (alexalkor/olx-free-stuff-bot,
// data/offers.json), same pattern as warsaw-events-worker but scoped to just
// receive + store + GitHub-backup. No Telegram-facing bot logic here yet —
// that's a separate, bigger project if/when you want the sibling bot built.
//
// Required setup in the Cloudflare dashboard for this Worker:
//   Bindings:
//     - KV namespace binding named OFFERS_KV  (create a new KV namespace, e.g. "olx_offers_kv")
//   Variables (Settings > Variables):
//     - GITHUB_REPO = "alexalkor/olx-free-stuff-bot"        (plain text var)
//     - GITHUB_FILE = "data/offers.json"                     (plain text var)
//   Secrets (Settings > Variables > "Encrypt" / Secrets):
//     - WEBHOOK_SECRET = <shared secret — must match the X-Secret header the
//                         scheduled task sends. Use the value the operator gave you.>
//     - GITHUB_PAT      = <a GitHub Personal Access Token, fine-grained, scoped
//                         to ONLY the olx-free-stuff-bot repo, with
//                         "Contents: Read and write" permission>
//
// Endpoints:
//   GET  /health   -> liveness check
//   GET  /offers   -> returns the last-stored payload from KV (for debugging / a future bot)
//   POST /offers   -> body: {"listings":[{title,link,description,posted_at}, ...], "date":"YYYY-MM-DD"}
//                     header: X-Secret: <WEBHOOK_SECRET>
//                     stores to KV, then backs up to GitHub as data/offers.json
//
// NOTE: this file is a manually-maintained mirror of the Worker as edited in
// the Cloudflare dashboard's Quick Editor. The dashboard copy is the source
// of truth — update this file by hand after making changes there, it is not
// auto-synced.

const VERSION = "v1.0-olx-worker";

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

async function githubBackup(env, contentObj) {
  const repo = env.GITHUB_REPO || "alexalkor/olx-free-stuff-bot";
  const path = env.GITHUB_FILE || "data/offers.json";
  const apiUrl = `https://api.github.com/repos/${repo}/contents/${path}`;
  const headers = {
    "Authorization": `Bearer ${env.GITHUB_PAT}`,
    "Accept": "application/vnd.github+json",
    "User-Agent": "olx-free-stuff-worker",
  };

  // 1. Look up current file sha (needed to update an existing file)
  let sha;
  const getResp = await fetch(apiUrl, { headers });
  if (getResp.status === 200) {
    const cur = await getResp.json();
    sha = cur.sha;
  } else if (getResp.status !== 404) {
    const errText = await getResp.text();
    return { status: getResp.status, message: `GitHub GET failed: ${errText.slice(0, 200)}` };
  }

  // 2. PUT the new content
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

    return json({ ok: false, error: "not found" }, 404);
  },
};
