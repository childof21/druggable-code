// Local end-to-end test of api/chat.js (run from ~/druggable-code-bot)
import fs from "fs";

const home = process.env.HOME;
process.env.EMBED_URL = "http://127.0.0.1:8501/embed";
process.env.EMBED_TOKEN = fs.readFileSync(`${home}/embed-svc/.env`, "utf8").match(/EMBED_TOKEN=(.*)/)[1];
process.env.DEEPSEEK_API_KEY = fs.readFileSync(`${home}/.hermes/profiles/mohammad/.env`, "utf8").match(/DEEPSEEK_API_KEY=(.*)/)[1];

const { default: handler } = await import("./api/chat.js");

function run(question) {
  return new Promise((resolve) => {
    const out = [];
    const res = {
      writeHead() {},
      write(d) { out.push(d); },
      end() { resolve(out.join("")); },
    };
    handler({ method: "POST", headers: { "x-forwarded-for": "127.0.0.1" }, body: JSON.stringify({ question }) }, res);
  });
}

const q = "RFdiffusion و ProteinMPNN چطور طراحی آنتی بادی انجام میدهند؟";
const sse = await run(q);
// parse events
const events = sse.split("\n").filter((l) => l.startsWith("data: ")).map((l) => JSON.parse(l.slice(6)));
let text = "";
let sources = null;
for (const ev of events) {
  if (ev.t) text += ev.t;
  if (ev.sources) sources = ev.sources;
  if (ev.error) { console.log("ERROR:", ev.error); process.exit(1); }
}
console.log("=== ANSWER ===");
console.log(text.slice(0, 1200));
console.log("=== SOURCES ===");
console.log(sources ? sources.map((s) => `پست ${s.id}: ${s.title.slice(0, 50)}`).join("\n") : "(none)");
console.log("=== DONE:", events.length, "events, answer chars:", text.length);
