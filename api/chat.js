// Vercel serverless function: RAG chat for druggable-code.vercel.app
// 1. embed the question via the LOCAL embedding service on our server (202.133.89.191:8501)
// 2. cosine top-5 over bundled vectors.bin + chunks.json
// 3. stream a grounded Persian answer from DeepSeek (SSE), then a sources event.
import fs from "fs";
import path from "path";

const DATA = path.join(process.cwd(), "data");
const CHUNKS = JSON.parse(fs.readFileSync(path.join(DATA, "chunks.json"), "utf8"));
const BUF = fs.readFileSync(path.join(DATA, "vectors.bin"));
const DIM = CHUNKS.dim;
const N = CHUNKS.chunks.length;
const MATRIX = new Float32Array(BUF.buffer, BUF.byteOffset, N * DIM); // rows = chunks

const EMBED_URL = process.env.EMBED_URL || "http://127.0.0.1:8501/embed";
const EMBED_TOKEN = process.env.EMBED_TOKEN || "";
const DEEPSEEK_KEY = process.env.DEEPSEEK_API_KEY || "";
const MODEL = "deepseek-chat";

// in-memory rate limit (per IP): 6 requests / 60s
const hits = new Map();
function rateLimited(ip) {
  const now = Date.now();
  const w = hits.get(ip) || [];
  const recent = w.filter((t) => now - t < 60_000);
  if (recent.length >= 6) return true;
  recent.push(now);
  hits.set(ip, recent);
  return false;
}

async function embedQuery(text) {
  const resp = await fetch(EMBED_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Embed-Token": EMBED_TOKEN },
    body: JSON.stringify({ texts: [text], prefix: "query" }),
  });
  if (!resp.ok) throw new Error(`embed svc ${resp.status}`);
  const data = await resp.json();
  return Float32Array.from(data.vectors[0]);
}

const STOP = new Set([
  "و","با","از","که","در","به","را","این","آن","یک","برای","چطور","چگونه","است","می","شود","شد",
  "های","ها","چه","کدام","توی","روی","شما","ما","میکند","میشود","درباره","خود","هم","نیز","باید",
  "میتوان","میتواند","کردن","انجام","طراحی","آنتی","بادی","هوش","مصنوعی","بهترین","مهم","بزرگ",
  "بوده","بود","نشان","میدهد","داد","گفت","کرد","کند","باش","باشد","مثل","مانند","هر","همه",
]);

function queryTerms(q) {
  return q
    .toLowerCase()
    .split(/[^a-z0-9\u0600-\u06FF]+/)
    .filter((t) => t.length >= 3 && !STOP.has(t) && !/^\d+$/.test(t));
}

function topK(qvec, question, k) {
  const qt = queryTerms(question);
  const scored = new Array(N);
  for (let r = 0; r < N; r++) {
    let dot = 0;
    const off = r * DIM;
    for (let c = 0; c < DIM; c++) dot += qvec[c] * MATRIX[off + c];
    let lex = 0;
    if (qt.length) {
      const t = CHUNKS.chunks[r].text.toLowerCase();
      for (const w of qt) if (t.includes(w)) lex++;
    }
    // hybrid: dense cosine + lexical term boost (MiniLM is STS-tuned; rare
    // technical terms like RFdiffusion need lexical weight)
    scored[r] = [dot * (1 + 0.4 * lex), dot, lex, r];
  }
  scored.sort((a, b) => b[0] - a[0]);
  // dedup: max 1 chunk per post for context diversity
  const seen = new Set();
  const out = [];
  for (const [h, dot, lex, r] of scored) {
    const pid = CHUNKS.chunks[r].post_id;
    if (seen.has(pid)) continue;
    seen.add(pid);
    out.push({ score: h, chunk: CHUNKS.chunks[r] });
    if (out.length === k) break;
  }
  return out;
}

function buildPrompt(question, top) {
  const context = top
    .map((t) => `[پست ${t.chunk.post_id}] ${t.chunk.text}`)
    .join("\n\n---\n\n");
  return [
    "تو دستیار هوشمند بلاگ «Druggable Code» هستی — بلاگ فارسی دکتر افشاری و دکتر کاظمی درباره ایمونولوژی، مهندسی دارو و هوش مصنوعی در پزشکی.",
    "قوانین:",
    "1. «زمینه» زیر (بخش‌هایی از پست‌های بلاگ) منبع اصلی توست؛ هر جا از آن استفاده کردی با فرمت (پست ۳۴) ارجاع بده.",
    "2. اگر زمینه کامل نبود، می‌توانی از دانش عمومی خودت هم پاسخ را تکمیل کنی (پاسخ بدون ارجاع) — اما هرگز برای ادعایی که از بلاگ است ارجاع جعلی نساز و هرگز چیزی خارج از زمینه را به بلاگ نسبت نده.",
    "3. اگر سؤال کاملاً خارج از حیطه بلاگ بود، باز هم با دانش خودت کمک‌کننده پاسخ بده و در پایان بگو «این موضوع در بلاگ پوشش داده نشده، ولی موضوعات مرتبط در بلاگ: ...»",
    "4. فارسی روان و آموزشی بنویس؛ اصطلاحات تخصصی را لاتین نگه دار.",
    "5. پاسخ موجز: حداکثر ~۳۰۰ کلمه، پاراگراف‌های کوتاه، نکات کلیدی را با **بولد** مشخص کن.",
    "",
    "زمینه (منبع اصلی):",
    context,
    "",
    `سؤال کاربر: ${question}`,
  ].join("\n");
}

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(405).json({ error: "POST only" });
  const ip = (req.headers["x-forwarded-for"] || "?").split(",")[0].trim();
  if (rateLimited(ip)) return res.status(429).json({ error: "rate limited — کمی صبر کن" });

  let question = "";
  try {
    // Vercel's Node runtime auto-parses JSON bodies (req.body is an object);
    // local dev passes a raw string — handle both.
    const body = typeof req.body === "string" ? JSON.parse(req.body) : (req.body || {});
    question = (body.question || "").trim().slice(0, 1200);
  } catch {
    return res.status(400).json({ error: "bad json" });
  }
  if (!question) return res.status(400).json({ error: "empty question" });

  res.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });
  const send = (obj) => res.write(`data: ${JSON.stringify(obj)}\n\n`);

  try {
    const qvec = await embedQuery(question);
    const top = topK(qvec, question, 5);
    const prompt = buildPrompt(question, top);

    const upstream = await fetch("https://api.deepseek.com/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${DEEPSEEK_KEY}` },
      body: JSON.stringify({
        model: MODEL,
        messages: [
          { role: "system", content: "پاسخ را فقط به فارسی بده، آموزشی و دقیق." },
          { role: "user", content: prompt },
        ],
        stream: true,
        max_tokens: 1200,
        temperature: 0.4,
      }),
    });
    if (!upstream.ok || !upstream.body) {
      throw new Error(`deepseek ${upstream.status}: ${(await upstream.text()).slice(0, 200)}`);
    }
    const reader = upstream.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() || "";
      for (const line of lines) {
        const s = line.trim();
        if (!s.startsWith("data:")) continue;
        const payload = s.slice(5).trim();
        if (payload === "[DONE]") continue;
        try {
          const j = JSON.parse(payload);
          const delta = j.choices?.[0]?.delta?.content;
          if (delta) send({ t: delta });
        } catch {}
      }
    }
    // sources event
    send({ sources: top.map((t) => ({ id: t.chunk.post_id, title: t.chunk.post_title })) });
  } catch (e) {
    send({ error: `خطای موقت: ${e.message}` });
  }
  res.end();
}
