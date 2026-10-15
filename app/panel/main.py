"""Web panel + test playground for the dialog engine.
Run: .venv/bin/uvicorn app.panel.main:app --host 127.0.0.1 --port 8000
Playground UI: http://127.0.0.1:8000/
"""
import json
import os
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from app import config
from app.db import (init_db, get_conn, upsert_tg_lead,
                    get_global_mode, set_global_mode)
from app.dialog_engine import DialogEngine

app = FastAPI(title="Leadgen Operator Panel")
engine = DialogEngine()
STATIC = os.path.join(os.path.dirname(__file__), "static")

@app.on_event("startup")
def _startup():
    init_db()

@app.get("/health")
def health():
    return {"status": "ok", "model": config.GEMINI_MODEL}

@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "playground.html"), headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})

# ===== Playground =====

@app.post("/api/pg/sessions")
def pg_new_session():
    with get_conn() as c:
        cur = c.execute("INSERT INTO pg_sessions DEFAULT VALUES")
        return {"id": cur.lastrowid}

@app.get("/api/pg/sessions")
def pg_sessions():
    with get_conn() as c:
        rows = c.execute(
            "SELECT s.id, s.created_at, COUNT(m.id) AS n FROM pg_sessions s "
            "LEFT JOIN pg_messages m ON m.session_id = s.id "
            "GROUP BY s.id ORDER BY s.id DESC").fetchall()
        return {"sessions": [dict(r) for r in rows]}

@app.get("/api/pg/{sid}/messages")
def pg_messages(sid: int):
    with get_conn() as c:
        rows = c.execute(
            "SELECT id, role, text, variants, qualification, created_at "
            "FROM pg_messages WHERE session_id = ? ORDER BY id", (sid,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["variants"] = json.loads(d["variants"]) if d["variants"] else None
            d["qualification"] = json.loads(d["qualification"]) if d["qualification"] else None
            out.append(d)
        return {"messages": out}

@app.post("/api/pg/{sid}/lead")
async def pg_lead(sid: int, req: Request):
    data = await req.json()
    text = (data.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    with get_conn() as c:
        c.execute("INSERT INTO pg_messages (session_id, role, text) VALUES (?, 'lead', ?)", (sid, text))
        rows = c.execute(
            "SELECT role, text, variants FROM pg_messages WHERE session_id = ? ORDER BY id",
            (sid,)).fetchall()
    history = [
        {"role": "user" if r["role"] == "lead" else "assistant", "text": r["text"]}
        for r in rows[:-1] if not (r["role"] == "operator" and r["variants"])
    ]
    try:
        res = engine.suggest(history, text)
    except Exception as e:
        return JSONResponse({"error": "%s: %s" % (type(e).__name__, str(e)[:300])}, status_code=502)
    variants = res.get("variants") or []
    qual = res.get("qualification") or {}
    with get_conn() as c:
        c.execute("DELETE FROM pg_messages WHERE session_id = ? AND role = 'operator' "
                  "AND variants IS NOT NULL", (sid,))
        cur = c.execute(
            "INSERT INTO pg_messages (session_id, role, text, variants, qualification) "
            "VALUES (?, 'operator', ?, ?, ?)",
            (sid, variants[0] if variants else "", json.dumps(variants, ensure_ascii=False),
             json.dumps(qual, ensure_ascii=False)))
        return {"message_id": cur.lastrowid, "variants": variants, "qualification": qual}

@app.post("/api/pg/{sid}/pick")
async def pg_pick(sid: int, req: Request):
    data = await req.json()
    idx = data.get("index")
    custom = (data.get("custom") or "").strip()
    with get_conn() as c:
        if custom:
            text = custom
        else:
            row = c.execute(
                "SELECT variants FROM pg_messages WHERE session_id = ? AND role = 'operator' "
                "AND variants IS NOT NULL ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
            if not row:
                return JSONResponse({"error": "no variants"}, status_code=400)
            variants = json.loads(row["variants"])
            try:
                text = variants[int(idx)]
            except Exception:
                return JSONResponse({"error": "bad index"}, status_code=400)
        c.execute("UPDATE pg_messages SET text = ?, variants = NULL WHERE id = ("
                  "SELECT id FROM pg_messages WHERE session_id = ? AND role = 'operator' "
                  "AND variants IS NOT NULL ORDER BY id DESC LIMIT 1)", (text, sid))
        return {"picked": text}

@app.post("/api/pg/{sid}/operator")
async def pg_operator(sid: int, req: Request):
    """Send a message as the operator manually (no engine call)."""
    data = await req.json()
    text = (data.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO pg_messages (session_id, role, text) VALUES (?, 'operator', ?)",
            (sid, text))
        return {"id": cur.lastrowid, "text": text}

@app.post("/api/pg/{sid}/regen")
async def pg_regen(sid: int):
    """Re-generate 3 variants for the last lead message."""
    with get_conn() as c:
        rows = c.execute(
            "SELECT role, text, variants FROM pg_messages WHERE session_id = ? ORDER BY id",
            (sid,)).fetchall()
    lead_idx = [i for i, r in enumerate(rows) if r["role"] == "lead"]
    if not lead_idx:
        return JSONResponse({"error": "no lead message yet"}, status_code=400)
    last = lead_idx[-1]
    history = [
        {"role": "user" if r["role"] == "lead" else "assistant", "text": r["text"]}
        for r in rows[:last] if not (r["role"] == "operator" and r["variants"])
    ]
    try:
        res = engine.suggest(history, rows[last]["text"])
    except Exception as e:
        return JSONResponse({"error": "%s: %s" % (type(e).__name__, str(e)[:300])}, status_code=502)
    variants = res.get("variants") or []
    qual = res.get("qualification") or {}
    with get_conn() as c:
        c.execute("DELETE FROM pg_messages WHERE session_id = ? AND role = 'operator' "
                  "AND variants IS NOT NULL", (sid,))
        cur = c.execute(
            "INSERT INTO pg_messages (session_id, role, text, variants, qualification) "
            "VALUES (?, 'operator', ?, ?, ?)",
            (sid, variants[0] if variants else "", json.dumps(variants, ensure_ascii=False),
             json.dumps(qual, ensure_ascii=False)))
        return {"message_id": cur.lastrowid, "variants": variants, "qualification": qual}

@app.post("/api/pg/{sid}/draft")
async def pg_draft(sid: int, req: Request):
    """Generate 3 reply variants from the operator's brief gist (stored as a pickable draft)."""
    data = await req.json()
    gist = (data.get("text") or "").strip()
    if not gist:
        return JSONResponse({"error": "empty text"}, status_code=400)
    with get_conn() as c:
        rows = c.execute(
            "SELECT role, text, variants FROM pg_messages WHERE session_id = ? ORDER BY id",
            (sid,)).fetchall()
    history = [
        {"role": "user" if r["role"] == "lead" else "assistant", "text": r["text"]}
        for r in rows if not (r["role"] == "operator" and r["variants"])
    ]
    try:
        res = engine.suggest_from_draft(history, gist)
    except Exception as e:
        return JSONResponse({"error": "%s: %s" % (type(e).__name__, str(e)[:300])}, status_code=502)
    variants = res.get("variants") or []
    qual = res.get("qualification") or {}
    with get_conn() as c:
        c.execute("DELETE FROM pg_messages WHERE session_id = ? AND role = 'operator' "
                  "AND variants IS NOT NULL", (sid,))
        cur = c.execute(
            "INSERT INTO pg_messages (session_id, role, text, variants, qualification) "
            "VALUES (?, 'operator', ?, ?, ?)",
            (sid, variants[0] if variants else "", json.dumps(variants, ensure_ascii=False),
             json.dumps(qual, ensure_ascii=False)))
        return {"message_id": cur.lastrowid, "variants": variants, "qualification": qual}

# ===== Leads panel (Telegram + YetReply) =====

from app.yr_listener import run_listener as yr_run_listener
from app.yr_listener import send_via_yetreply
from app.yr_client import YetReplyClient
from app.tg_listener import run_listener as tg_run_listener
from app.tg_listener import send_with_typing
from app.tg_listener import make_client as tg_make_client

tg_client = None
yr_client = None

@app.get("/leads")
def leads_page():
    return FileResponse(os.path.join(STATIC, "leads.html"), headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})

@app.get("/api/leads")
def api_leads(source: str = None):
    with get_conn() as c:
        if source and source in ("tg", "yr"):
            rows = c.execute(
                "SELECT * FROM leads WHERE source = ? ORDER BY CASE status WHEN 'in_work' THEN 0 "
                "WHEN 'new' THEN 1 WHEN 'paused' THEN 2 WHEN 'call_booked' THEN 3 ELSE 4 END, "
                "last_activity_at DESC", (source,)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM leads ORDER BY CASE status WHEN 'in_work' THEN 0 "
                "WHEN 'new' THEN 1 WHEN 'paused' THEN 2 WHEN 'call_booked' THEN 3 ELSE 4 END, "
                "last_activity_at DESC").fetchall()
        return {"leads": [dict(r) for r in rows]}

def _load_first_messages():
    """Load canonical first-touch variants from kb/script.md."""
    import re as _re
    path = os.path.join(config.KB_DIR, "script.md")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    marker = "## Первое касание"
    if marker not in content:
        return []
    section = content.split(marker, 1)[1].split("\n## ", 1)[0]
    matches = _re.findall(r"^\s*\d+\.\s*«([^»]+)»\s*$", section, _re.MULTILINE)
    return [_re.sub(r"\s+", " ", message).strip() for message in matches]


_FIRST_TOUCH_FORBIDDEN = (
    "hr group", "hr-group", "эйчар груп", "хр груп", "компан",
    "меня зовут", "представляю", "мы занимаемся", "мы помогаем",
    "можем помочь", "наши услуги", "наше агентство", "кадровое агентство",
    "[имя]", "(имя)", "у многих", "в последнее время", "просадк",
    "отклик", "хэ-ха", "хх.ру", "2–3 недели", "2-3 недели",
)


def _normalize_first_touch(text):
    import re as _re
    text = _re.sub(r"\s+", " ", str(text or "")).strip()
    text = _re.sub(r"^(?:вариант\s*\d+|в\d+)\s*[:.)—-]\s*", "", text,
                   flags=_re.IGNORECASE)
    return text


def _is_safe_first_touch(text):
    normalized = _normalize_first_touch(text)
    lowered = normalized.lower().replace("ё", "е")
    forbidden = tuple(item.replace("ё", "е") for item in _FIRST_TOUCH_FORBIDDEN)
    return bool(normalized and "?" in normalized and len(normalized) <= 320
                and not any(item in lowered for item in forbidden)
                and "**" not in normalized and "#" not in normalized)


def _generate_first_messages():
    examples = _load_first_messages()
    if not examples:
        raise RuntimeError("no first-message examples in kb/script.md")
    for _attempt in range(2):
        result = engine.suggest_first_touch(examples)
        safe = []
        seen = set()
        for raw in (result.get("variants") or []):
            text = _normalize_first_touch(raw)
            key = text.lower()
            if _is_safe_first_touch(text) and key not in seen:
                safe.append(text)
                seen.add(key)
        if len(safe) >= 3:
            return safe[:3]
    raise ValueError("model did not produce 3 safe first-touch variants")


@app.post("/api/leads/add")
async def api_lead_add(req: Request):
    data = await req.json()
    ident = (data.get("identifier") or "").strip().lstrip("@")
    source = (data.get("source") or "tg").strip()
    if not ident:
        return JSONResponse({"error": "empty identifier"}, status_code=400)
    if source not in {"tg", "yr"}:
        return JSONResponse({"error": "bad source"}, status_code=400)

    if source == "tg":
        is_numeric = ident.lstrip("-").isdigit()
        lookup = int(ident) if is_numeric else ident
        user = None
        if tg_client is not None:
            try:
                user = await tg_client.get_users(lookup)
            except Exception as e:
                if not is_numeric:
                    return JSONResponse(
                        {"error": "Telegram user not found: %s" % str(e)[:180]},
                        status_code=400,
                    )
        elif not is_numeric:
            return JSONResponse({"error": "Telegram client not running"}, status_code=503)

        if user is not None:
            tg_id = user.id
            username = user.username
            name = " ".join(
                part for part in (user.first_name, user.last_name) if part
            ).strip() or username or str(tg_id)
        else:
            tg_id = int(ident)
            username = None
            name = ident

        with get_conn() as c:
            lead, created = upsert_tg_lead(
                c, tg_id, username=username, name=name,
                new_status="in_work", force_status="in_work",
            )
        return {"id": lead["id"], "status": "in_work",
                "existed": not created, "resolved_tg_id": str(tg_id)}

    with get_conn() as c:
        row = c.execute(
            "SELECT id, status FROM leads WHERE source = 'yr' "
            "AND lower(CAST(tg_user_id AS TEXT)) = lower(?)", (ident,)
        ).fetchone()
        if row:
            c.execute("UPDATE leads SET status = 'in_work' WHERE id = ?", (row["id"],))
            return {"id": row["id"], "status": "in_work", "existed": True}
        cur = c.execute(
            "INSERT INTO leads (tg_user_id, name, status, source) VALUES (?,?,?, 'yr')",
            (ident, ident, "in_work"),
        )
        return {"id": cur.lastrowid, "status": "in_work", "existed": False}

@app.get("/api/leads/{lead_id}/messages")
def api_lead_messages(lead_id: int):
    with get_conn() as c:
        lead = c.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        rows = c.execute(
            "SELECT id, direction, text, variants, qualification, created_at "
            "FROM lead_messages WHERE lead_id = ? ORDER BY id", (lead_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["variants"] = json.loads(d["variants"]) if d["variants"] else None
            d["qualification"] = json.loads(d["qualification"]) if d["qualification"] else None
            out.append(d)
        return {"lead": dict(lead) if lead else None, "messages": out}

@app.get("/api/stats")
def api_stats():
    with get_conn() as c:
        total = c.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"]
        by_status = {r["status"]: r["n"] for r in c.execute(
            "SELECT status, COUNT(*) AS n FROM leads GROUP BY status")}
        today_in = c.execute(
            "SELECT COUNT(*) AS n FROM lead_messages WHERE direction = 'in' "
            "AND date(created_at) = date('now')").fetchone()["n"]
        today_out = c.execute(
            "SELECT COUNT(*) AS n FROM lead_messages WHERE direction = 'out' "
            "AND date(created_at) = date('now')").fetchone()["n"]
        drops = [dict(r) for r in c.execute(
            "SELECT drop_reason, COUNT(*) AS n FROM leads WHERE status = 'dropped' "
            "GROUP BY drop_reason")]
        by_source = {r["source"]: r["n"] for r in c.execute(
            "SELECT source, COUNT(*) AS n FROM leads GROUP BY source")}
        return {"total": total, "by_status": by_status,
                "today_in": today_in, "today_out": today_out, "drop_reasons": drops,
                "by_source": by_source}

@app.post("/api/leads/{lead_id}/status")
async def api_lead_status(lead_id: int, req: Request):
    data = await req.json()
    status = data.get("status")
    reason = data.get("drop_reason")
    allowed = {"new", "in_work", "paused", "dropped", "call_booked"}
    if status not in allowed:
        return JSONResponse({"error": "bad status"}, status_code=400)
    with get_conn() as c:
        c.execute("UPDATE leads SET status = ?, drop_reason = ? WHERE id = ?",
                  (status, reason, lead_id))
        if status == "in_work":
            c.execute("UPDATE leads SET dialog_closed = 0, closed_reason = NULL, "
                      "refusals = 0 WHERE id = ?", (lead_id,))
        return {"ok": True}

@app.post("/api/leads/{lead_id}/resume")
async def api_lead_resume(lead_id: int):
    """Снова разрешить автоответы и обнулить счётчик отказов."""
    with get_conn() as c:
        c.execute("UPDATE leads SET dialog_closed = 0, closed_reason = NULL, "
                  "refusals = 0 WHERE id = ?", (lead_id,))
        return {"ok": True, "resumed": lead_id}

@app.get("/api/settings/mode")
def api_global_mode():
    return {"mode": get_global_mode()}


@app.post("/api/settings/mode")
async def api_set_global_mode(req: Request):
    data = await req.json()
    mode = data.get("mode")
    if mode not in {"auto", "manual"}:
        return JSONResponse({"error": "bad mode"}, status_code=400)
    set_global_mode(mode)
    return {"mode": mode}

@app.post("/api/leads/{lead_id}/suggest")
async def api_lead_suggest(lead_id: int):
    """Generate fresh first-touch variants or replies to the latest lead message."""
    with get_conn() as c:
        lead = c.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            return JSONResponse({"error": "lead not found"}, status_code=404)
        rows = c.execute(
            "SELECT direction, text FROM lead_messages WHERE lead_id = ? "
            "AND direction IN ('in','out') ORDER BY id", (lead_id,)).fetchall()
    if not rows:
        try:
            variants = _generate_first_messages()
        except Exception as e:
            return JSONResponse({"error": "first-touch generation failed: %s" % str(e)[:250]},
                                status_code=502)
        qual = {}
    else:
        in_idx = [i for i, r in enumerate(rows) if r["direction"] == "in"]
        if not in_idx:
            return JSONResponse({"error": "no incoming message to reply to"}, status_code=400)
        last = in_idx[-1]
        history = [
            {"role": "user" if r["direction"] == "in" else "assistant", "text": r["text"]}
            for r in rows[:last]
        ]
        try:
            res = engine.suggest(history, rows[last]["text"])
        except Exception as e:
            return JSONResponse({"error": "%s: %s" % (type(e).__name__, str(e)[:300])}, status_code=502)
        variants = res.get("variants") or []
        qual = res.get("qualification") or {}
    with get_conn() as c:
        c.execute("DELETE FROM lead_messages WHERE lead_id = ? AND direction = 'draft' "
                  "AND variants IS NOT NULL", (lead_id,))
        cur = c.execute(
            "INSERT INTO lead_messages (lead_id, direction, text, variants, qualification) "
            "VALUES (?,?,?,?,?)",
            (lead_id, "draft", variants[0] if variants else "",
             json.dumps(variants, ensure_ascii=False),
             json.dumps(qual, ensure_ascii=False) if qual else None))
        return {"message_id": cur.lastrowid, "variants": variants, "qualification": qual}


@app.post("/api/leads/{lead_id}/draft")
async def api_lead_draft(lead_id: int, req: Request):
    """Expand operator's brief gist into 3 full reply variants."""
    data = await req.json()
    gist = (data.get("text") or "").strip()
    if not gist:
        return JSONResponse({"error": "empty text"}, status_code=400)
    with get_conn() as c:
        lead = c.execute("SELECT id FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            return JSONResponse({"error": "lead not found"}, status_code=404)
        rows = c.execute(
            "SELECT direction, text FROM lead_messages WHERE lead_id = ? "
            "AND direction IN ('in','out') ORDER BY id", (lead_id,)).fetchall()
    history = [
        {"role": "user" if r["direction"] == "in" else "assistant", "text": r["text"]}
        for r in rows
    ]
    try:
        res = engine.suggest_from_draft(history, gist)
    except Exception as e:
        return JSONResponse({"error": "%s: %s" % (type(e).__name__, str(e)[:300])}, status_code=502)
    variants = res.get("variants") or []
    qual = res.get("qualification") or {}
    with get_conn() as c:
        c.execute("DELETE FROM lead_messages WHERE lead_id = ? AND direction = 'draft' "
                  "AND variants IS NOT NULL", (lead_id,))
        cur = c.execute(
            "INSERT INTO lead_messages (lead_id, direction, text, variants, qualification) "
            "VALUES (?,?,?,?,?)",
            (lead_id, "draft", variants[0] if variants else "",
             json.dumps(variants, ensure_ascii=False),
             json.dumps(qual, ensure_ascii=False) if qual else None))
        return {"message_id": cur.lastrowid, "variants": variants, "qualification": qual}


@app.post("/api/leads/{lead_id}/send")
async def api_lead_send(lead_id: int, req: Request):
    data = await req.json()
    custom = (data.get("custom") or "").strip()
    with get_conn() as c:
        lead = c.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            return JSONResponse({"error": "lead not found"}, status_code=404)
        lead = dict(lead)
        if custom:
            text = custom
        else:
            row = c.execute(
                "SELECT variants FROM lead_messages WHERE lead_id = ? AND direction = 'draft' "
                "AND variants IS NOT NULL ORDER BY id DESC LIMIT 1", (lead_id,)).fetchone()
            if not row:
                return JSONResponse({"error": "no variants"}, status_code=400)
            variants = json.loads(row["variants"])
            try:
                text = variants[int(data.get("index"))]
            except Exception:
                return JSONResponse({"error": "bad index"}, status_code=400)
    src = lead.get("source", "tg")
    if src == "yr":
        if yr_client is None:
            return JSONResponse({"error": "YetReply client not running"}, status_code=503)
        try:
            acct = lead.get("account_id")
            await send_via_yetreply(yr_client, lead["tg_user_id"], text, acct)
        except Exception as e:
            return JSONResponse({"error": "YetReply send failed: %s" % str(e)[:200]}, status_code=502)
    else:
        if tg_client is None:
            return JSONResponse({"error": "Telegram client not running"}, status_code=503)
        try:
            chat_id = int(lead["tg_user_id"])
        except (ValueError, TypeError):
            chat_id = lead["tg_user_id"]
        try:
            await send_with_typing(tg_client, chat_id, text)
        except Exception as e:
            return JSONResponse({"error": "Telegram send failed: %s" % str(e)[:200]}, status_code=502)
    with get_conn() as c:
        c.execute("INSERT INTO lead_messages (lead_id, direction, text) VALUES (?,?,?)",
                  (lead_id, "out", text))
        c.execute("DELETE FROM lead_messages WHERE lead_id = ? AND direction = 'draft'",
                  (lead_id,))
        c.execute("UPDATE leads SET dialog_closed = 0, closed_reason = NULL WHERE id = ?",
                  (lead_id,))
        return {"sent": text}

@app.on_event("startup")
async def _start_listeners():
    global tg_client, yr_client
    tg_session = os.path.join(config.BASE_DIR, config.SESSION_NAME + ".session")
    if os.path.exists(tg_session):
        try:
            tg_client = tg_make_client()
            asyncio.create_task(tg_run_listener(engine, tg_client))
            print("[tg] listener starting...")
        except Exception as e:
            print(f"[tg] Failed to start: {e}")
    else:
        print("[tg] No session file, skipping TG listener")
    if config.YETREPLY_USERNAME and config.YETREPLY_PASSWORD:
        try:
            yr_client = YetReplyClient()
            asyncio.create_task(yr_run_listener(engine, yr_client))
            print("[yr] listener starting...")
        except Exception as e:
            print(f"[yr] Failed to start: {e}")
    else:
        print("[yr] No credentials, skipping YR listener")
