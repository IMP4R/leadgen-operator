"""YetReply listener — runs alongside tg_listener.

Listens for dialog_activity events via WebSocket,
loads new messages from YetReply API, processes them through DialogEngine.
Reply rules (two-refusal tactic, anti-loop) live in app/reply_policy.py.
"""
import asyncio
import json

from app import db as DB
from app import reply_flow
from app.yr_client import YetReplyClient


async def send_via_yetreply(client, chat_id, text, account_id=None):
    """Send a message through YetReply API."""
    await client.send_message(str(chat_id), text, account_id)


def _is_outgoing(m):
    """Check if a YetReply message is outgoing (from operator)."""
    if "from_me" in m:
        return bool(m["from_me"])
    if "is_outgoing" in m:
        return bool(m["is_outgoing"])
    if "direction" in m:
        return m["direction"] in ("out", "outgoing")
    return False


def _msg_text(m):
    """Extract text from a YetReply message."""
    return (m.get("text") or m.get("message") or m.get("content") or "").strip()


async def process_dialog_activity(engine, client, chat_id, account_id=None):
    """Called on dialog_activity WebSocket event.

    Loads recent messages, finds the last incoming message,
    processes it through the shared reply pipeline if it is new.
    """
    if account_id is None:
        account_id = await client.get_first_active_account_id()

    try:
        messages = await client.get_messages(str(chat_id), account_id, limit=50)
    except Exception as e:
        print(f"[yr] Error loading messages for {chat_id}: {e}")
        return

    if not messages:
        return

    incoming = [m for m in messages if not _is_outgoing(m)]
    if not incoming:
        return

    last = incoming[-1]
    text = _msg_text(last)
    if not text:
        return

    chat_id_str = str(chat_id)

    with DB.get_conn() as c:
        row = c.execute(
            "SELECT * FROM leads WHERE tg_user_id = ?", (chat_id_str,)
        ).fetchone()
        if row:
            lead = dict(row)
            created = False
        else:
            display_name = (
                last.get("display_name") or last.get("name") or chat_id_str
            )
            cur = c.execute(
                "INSERT INTO leads (tg_user_id, name, status, account_id, mode, source) "
                "VALUES (?,?,?,?,?,?)",
                (chat_id_str, display_name, "new", account_id, "auto", "yr"),
            )
            lead = {"id": cur.lastrowid, "status": "new", "mode": "auto"}
            created = True

        recent = c.execute(
            "SELECT id FROM lead_messages WHERE lead_id = ? AND direction = 'in' "
            "AND text = ? ORDER BY id DESC LIMIT 1",
            (lead["id"], text),
        ).fetchone()
        if recent:
            return

        c.execute(
            "INSERT INTO lead_messages (lead_id, direction, text) VALUES (?,?,?)",
            (lead["id"], "in", text),
        )
        c.execute(
            "UPDATE leads SET last_activity_at = CURRENT_TIMESTAMP WHERE id = ?",
            (lead["id"],),
        )

        if lead["status"] != "in_work":
            return {"stored_only": True, "created": created, "status": lead["status"]}

        gate = reply_flow.gate(c, lead, text, tag="yr")
        if not gate["reply"]:
            return {"stored_only": True, "closed": True, "reason": gate["reason"]}
        lead = gate["lead"]

    async def _send(body):
        await send_via_yetreply(client, chat_id, body, account_id)

    return await reply_flow.run_reply(engine, lead, text, _send, tag="yr")


async def run_listener(engine, client):
    """Start YetReply WebSocket listener."""
    if not client.token:
        print("[yr] Logging in to YetReply...")
        await client.login()
        u = client.user.get("username") if client.user else "unknown"
        print(f"[yr] Logged in as {u}")

    account_id = await client.get_first_active_account_id()
    print(f"[yr] Using account_id={account_id}")

    async def on_activity(chat_id, acct_id):
        try:
            await process_dialog_activity(
                engine, client, chat_id, acct_id or account_id
            )
        except Exception as e:
            print(f"[yr] Activity error: {type(e).__name__}: {str(e)[:200]}")

    print("[yr] Starting WebSocket listener...")
    await client.ws_listen(on_activity)
