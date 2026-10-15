"""Telegram listener (Kurigram) — runs inside the panel process.

Only processes DMs from leads with status 'in_work'.
Everyone else is stored silently (never answered, never deleted).
Typing simulation: 'печатает…' action + human-ish delay (~2x human CPM).
Reply rules (two-refusal tactic, anti-loop) live in app/reply_policy.py.
"""
import asyncio
import os
import random

from pyrogram import Client, filters, enums

from app import config
from app import db as DB
from app import reply_flow
from app import reply_policy as policy

CPM = int(os.getenv("TYPING_CPM", "500"))  # ~2x human typing speed, chars/min

# Совместимость со старым кодом и тестами.
_ABUSE_MARKERS = policy._ABUSE_MARKERS
_looks_abusive = policy.looks_abusive
_abuse_fallback = policy.abuse_fallback


def make_client() -> Client:
    return Client(config.SESSION_NAME, api_id=config.API_ID,
                  api_hash=config.API_HASH, workdir=config.BASE_DIR)


async def send_with_typing(client, chat_id, text):
    """Show 'печатает…' while waiting a human-like delay, then send."""
    delay = min(25.0, max(3.0, len(text) / CPM * 60.0))
    delay *= random.uniform(0.85, 1.15)
    waited = 0.0
    while waited < delay:
        try:
            await client.send_chat_action(chat_id, enums.ChatAction.TYPING)
        except Exception:
            break
        step = min(4.0, delay - waited)
        await asyncio.sleep(step)
        waited += step
    await client.send_message(chat_id, text)


async def process_incoming(client, engine, msg):
    """Full pipeline for one incoming DM. Safe to call from listener or tests."""
    if msg.outgoing or msg.service or not (msg.text or "").strip():
        return
    user = msg.from_user
    tg_id = user.id if user else msg.chat.id
    text = msg.text.strip()
    username = user.username if user else None
    if user:
        name = " ".join(part for part in (user.first_name, user.last_name) if part).strip()
    else:
        name = "unknown"

    with DB.get_conn() as c:
        lead, created = DB.upsert_tg_lead(
            c, tg_id, username=username, name=name, new_status="new"
        )
        c.execute("INSERT INTO lead_messages (lead_id, direction, text) VALUES (?,?,?)",
                  (lead["id"], "in", text))
        c.execute("UPDATE leads SET last_activity_at = CURRENT_TIMESTAMP WHERE id = ?",
                  (lead["id"],))
        if lead["status"] != "in_work":
            return {"stored_only": True, "created": created, "status": lead["status"]}
        gate = reply_flow.gate(c, lead, text, tag="tg")
        if not gate["reply"]:
            return {"stored_only": True, "closed": True, "reason": gate["reason"]}
        lead = gate["lead"]

    async def _send(body):
        await send_with_typing(client, tg_id, body)

    return await reply_flow.run_reply(engine, lead, text, _send, tag="tg")


async def run_listener(engine, client=None):
    client = client or make_client()

    @client.on_message(filters.private & filters.incoming & ~filters.me)
    async def _on_msg(_, msg):
        try:
            await process_incoming(client, engine, msg)
        except Exception as e:
            print("listener error:", type(e).__name__, str(e)[:200])

    await client.start()
    print("tg listener started")
    await asyncio.get_event_loop().create_future()
