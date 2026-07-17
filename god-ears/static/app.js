/* GOD EARS terminal UI — polls the feed, streams answers, drives the 3 buttons. */

const term = document.getElementById("term");
const cmd = document.getElementById("cmd");
const pills = {
  state: document.getElementById("pill-state"),
  stt: document.getElementById("pill-stt"),
  llm: document.getElementById("pill-llm"),
  today: document.getElementById("pill-today"),
  sync: document.getElementById("pill-sync"),
};
const btns = {
  run: document.getElementById("btn-run"),
  pause: document.getElementById("btn-pause"),
  term: document.getElementById("btn-term"),
};

let lastFeedId = 0;
let dead = false;

/* ── rendering ─────────────────────────────────────────── */
function scrolledToBottom() {
  return term.scrollHeight - term.scrollTop - term.clientHeight < 60;
}
function addLine(cls, html) {
  const stick = scrolledToBottom();
  const div = document.createElement("div");
  div.className = "line " + cls;
  div.innerHTML = html;
  term.appendChild(div);
  if (stick) term.scrollTop = term.scrollHeight;
  return div;
}
const esc = (s) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function renderEvent(ev) {
  const t = `<span class="t">[${ev.t}]</span>`;
  switch (ev.kind) {
    case "heard":    addLine("heard", t + esc(ev.text)); break;
    case "wake":     addLine("wake", t + "&#x1F514; " + esc(ev.text)); break;
    case "question": addLine("question", t + "&#x1F3A4; " + esc(ev.text)); break;
    case "answer":   addLine("answer", t + esc(ev.text)); break;
    default:         addLine("system", t + esc(ev.text));
  }
}

/* ── live caption (uncommitted streaming words) ────────── */
const liveDiv = document.createElement("div");
liveDiv.className = "line live";
liveDiv.style.display = "none";

function updateLive(text) {
  const stick = scrolledToBottom();
  if (text) {
    liveDiv.textContent = text;
    liveDiv.style.display = "";
  } else {
    liveDiv.style.display = "none";
  }
  term.appendChild(liveDiv);   // keep it below the newest line
  if (stick) term.scrollTop = term.scrollHeight;
}

/* ── polling ───────────────────────────────────────────── */
async function pollFeed() {
  if (dead) return;
  try {
    const r = await fetch(`/api/feed?after=${lastFeedId}`);
    const data = await r.json();
    for (const ev of data.events) {
      renderEvent(ev);
      lastFeedId = ev.id;
    }
    updateLive(data.live || "");
  } catch { /* server gone or restarting */ }
}

async function pollStatus() {
  if (dead) return;
  try {
    const r = await fetch("/api/status");
    const s = await r.json();
    const st = s.ears.state;
    pills.state.className = "pill " + (st === "running" ? "listening" : st);
    pills.state.textContent =
      st === "running"
        ? (s.ears.speaking ? "SPEAKING" : s.ears.in_speech ? "HEARING ♪" : "LISTENING")
        : st === "paused" ? "PAUSED" : st.toUpperCase();
    pills.stt.textContent = "whisper: " +
      (s.stt.state === "ready" ? s.stt.model : s.stt.state + "…");
    pills.llm.textContent = "llm: " + s.llm;
    pills.today.textContent =
      `${s.today.words} words · ${s.today.segments} segments today`;
    pills.sync.textContent = s.sync.enabled
      ? "sync: on" + (s.sync.last_push ? " · pushed " + s.sync.last_push : "")
      : "sync: off";
    btns.run.disabled = st === "running";
    btns.pause.disabled = st !== "running";
  } catch {
    if (!dead) {
      pills.state.className = "pill dead";
      pills.state.textContent = "OFFLINE";
    }
  }
}
setInterval(pollFeed, 700);
setInterval(pollStatus, 2500);
pollFeed(); pollStatus();

/* ── the 3 buttons ─────────────────────────────────────── */
async function control(action) {
  try { await fetch("/api/control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  }); } catch {}
  pollStatus();
}
btns.run.onclick = () => control("run");
btns.pause.onclick = () => control("pause");
btns.term.onclick = async () => {
  if (!confirm("Terminate God Ears? The process exits — restart with run.bat.")) return;
  await control("terminate");
  dead = true;
  pills.state.className = "pill dead";
  pills.state.textContent = "TERMINATED";
  addLine("err", "process terminated — relaunch with run.bat");
  cmd.disabled = true;
  Object.values(btns).forEach((b) => (b.disabled = true));
};

/* ── commands ──────────────────────────────────────────── */
const HELP = [
  "just type a question    ask about anything heard today (or recent days)",
  "/tail [n]               last n transcript lines (default 20)",
  "/day YYYY-MM-DD         show that day's full transcript",
  "/days                   list all stored transcript days",
  "/sync                   push/pull transcripts with the cloud folder now",
  "/clear                  clear this screen",
  "/help                   this text",
].join("\n");

async function runCommand(input) {
  const [name, ...rest] = input.slice(1).split(/\s+/);
  try {
    if (name === "help") {
      addLine("out", esc(HELP));
    } else if (name === "clear") {
      term.innerHTML = "";
    } else if (name === "tail") {
      const n = parseInt(rest[0]) || 20;
      const text = await (await fetch("/api/transcript")).text();
      const lines = text.split("\n").filter((l) => l.startsWith("- "));
      addLine("out", esc(lines.slice(-n).join("\n") || "(nothing heard yet today)"));
    } else if (name === "day") {
      const text = await (await fetch(`/api/transcript?date=${rest[0] || ""}`)).text();
      addLine("out", esc(text));
    } else if (name === "days") {
      const d = await (await fetch("/api/days")).json();
      addLine("out", esc(d.days.join("\n") || "(no transcripts yet)"));
    } else if (name === "sync") {
      addLine("out", esc(await (await fetch("/api/sync", { method: "POST" })).text()));
    } else {
      addLine("err", `unknown command: /${esc(name)} — try /help`);
    }
  } catch (e) {
    addLine("err", "command failed: " + esc(String(e)));
  }
}

async function askQuestion(q) {
  const out = addLine("answer", "");
  out.classList.add("cursor-blink");
  try {
    const resp = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let text = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      text += dec.decode(value, { stream: true });
      out.textContent = text;
      if (scrolledToBottom()) term.scrollTop = term.scrollHeight;
    }
    if (!text.trim()) out.textContent = "(no answer)";
  } catch (e) {
    out.textContent = "brain unreachable: " + e;
    out.className = "line err";
  } finally {
    out.classList.remove("cursor-blink");
  }
}

/* ── input handling ────────────────────────────────────── */
const history = [];
let histPos = -1;

cmd.addEventListener("keydown", (e) => {
  if (e.key === "ArrowUp" && history.length) {
    histPos = Math.min(histPos + 1, history.length - 1);
    cmd.value = history[history.length - 1 - histPos];
    e.preventDefault();
  } else if (e.key === "ArrowDown") {
    histPos = Math.max(histPos - 1, -1);
    cmd.value = histPos < 0 ? "" : history[history.length - 1 - histPos];
    e.preventDefault();
  } else if (e.key === "Enter") {
    const input = cmd.value.trim();
    if (!input) return;
    history.push(input);
    histPos = -1;
    cmd.value = "";
    addLine("cmd", `<span class="ps">PS God-Ears&gt;</span> ${esc(input)}`);
    if (input.startsWith("/")) runCommand(input);
    else askQuestion(input);
  }
});

addLine("system", "GOD EARS terminal — type a question, or /help for commands.");
