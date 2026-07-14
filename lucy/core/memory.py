import os, json, datetime
from . import config

os.makedirs(config.MEMORY_DIR, exist_ok=True)
os.makedirs(config.SUMMARY_DIR, exist_ok=True)


def get_today_path():
    return os.path.join(config.MEMORY_DIR, f"{datetime.date.today().isoformat()}.json")


def load_today_memory():
    p = get_today_path()
    return json.load(open(p, "r", encoding="utf-8")) if os.path.exists(p) else []


def save_today_memory(history):
    with open(get_today_path(), "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def get_time_context():
    now = datetime.datetime.now()
    return f"Current Date & Time: {now.strftime('%A, %B %d, %Y at %H:%M')}. "


def get_past_context():
    ctx = []
    today = load_today_memory()
    if today:
        ctx.extend([f"{t.get('speaker') or 'User'}: {t['user']}\nAI: {t['ai']}" for t in today])
    summaries = sorted([f for f in os.listdir(config.SUMMARY_DIR) if f.endswith(".md")], reverse=True)
    for s in summaries[:2]:
        with open(os.path.join(config.SUMMARY_DIR, s), "r", encoding="utf-8") as f:
            ctx.append(f.read().strip())
    return "\n\n".join(ctx) if ctx else "No past conversations recorded."


def summarize_daily_log():
    import requests
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    y_path = os.path.join(config.MEMORY_DIR, f"{yesterday}.json")
    s_path = os.path.join(config.SUMMARY_DIR, f"{datetime.date.today().strftime('%Y-%m')}_summary.md")
    if not os.path.exists(y_path) or os.path.exists(s_path):
        return
    try:
        logs = json.load(open(y_path, "r", encoding="utf-8"))
        text = " ".join([f"U: {l['user']} AI: {l['ai']}" for l in logs])
        requests.post(config.OLLAMA_URL, json={
            "model": config.LLM_MODEL,
            "messages": [{"role": "user", "content": f"Summarize into 3-4 key points:\n{text}"}],
            "stream": False
        }, timeout=60)
    except Exception:
        pass
