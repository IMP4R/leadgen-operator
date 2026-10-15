"""Shared reply pipeline for the Telegram and YetReply listeners.

Один путь для обоих источников, чтобы правила не расходились:

1. gate()      — закрытый диалог отвечает только на реальный интерес.
2. run_reply() — генерация, тактика двух отказов, защита от повторов, отправка.
"""
import json

from app import db as DB
from app import reply_policy as policy

HISTORY_LIMIT = 24


def gate(conn, lead, text, tag="tg"):
    """Решить, отвечаем ли вообще, и при интересе вернуть диалог в работу."""
    lead = dict(lead)
    decision = policy.gate_incoming(lead, text)
    if not decision["reply"]:
        print("[%s] диалог закрыт (лид %s) — сообщение сохранено без ответа"
              % (tag, lead.get("id")))
        return {"reply": False, "reason": decision["reason"], "lead": lead}
    if decision["reopen"]:
        DB.set_dialog_state(conn, lead["id"], refusals=0, closed=False)
        lead.update(refusals=0, dialog_closed=0, closed_reason=None)
        print("[%s] интерес после закрытия — лид %s снова в работе"
              % (tag, lead.get("id")))
    return {"reply": True, "reason": decision["reason"], "lead": lead}


def _load_context(lead_id):
    """История без последнего входящего + последнее исходящее."""
    with DB.get_conn() as c:
        rows = c.execute(
            "SELECT direction, text FROM lead_messages "
            "WHERE lead_id = ? AND direction != 'draft' "
            "ORDER BY id DESC LIMIT ?", (lead_id, HISTORY_LIMIT)).fetchall()
        last_out = c.execute(
            "SELECT text FROM lead_messages WHERE lead_id = ? AND direction = 'out' "
            "ORDER BY id DESC LIMIT 1", (lead_id,)).fetchone()
    history = [
        {"role": "user" if r["direction"] == "in" else "assistant", "text": r["text"]}
        for r in reversed(rows[1:])
    ]
    return history, (last_out["text"] if last_out else "")


async def run_reply(engine, lead, text, send, tag="tg"):
    """Сгенерировать ответ по политике и отправить его, если режим авто."""
    lead = dict(lead)
    lead_id = lead["id"]
    history, last_out = _load_context(lead_id)

    abusive = policy.looks_abusive(text)
    if abusive:
        print("[%s] агрессивное сообщение — безопасный ответ без модели" % tag)
        res = policy.abuse_fallback()
    else:
        res = engine.suggest(history, text)
    variants = res.get("variants") or []
    qual = res.get("qualification") or {}

    plan = policy.plan_reply(
        text=text, qual=qual, variants=variants,
        refusals=int(lead.get("refusals") or 0),
        last_out=last_out, abusive=abusive,
    )

    if plan["action"] == "reengage":
        print("[%s] отказ №%d — пробуем вернуть интерес%s"
              % (tag, plan["attempt"], " (последняя попытка)" if plan["is_last"] else ""))
        strong, strong_qual = [], None
        for _ in range(2):
            try:
                again = engine.suggest_reengagement(
                    history, text, plan["attempt"], plan["is_last"])
            except Exception as e:
                print("[%s] попытка вернуть интерес не удалась: %s" % (tag, str(e)[:140]))
                break
            strong = policy.filter_reengagement(again.get("variants") or [], last_out)
            if strong:
                strong_qual = again.get("qualification")
                break
            print("[%s] варианты просили разрешения написать потом — перегенерирую" % tag)
        if not strong:
            strong = [policy.reengagement_fallback(plan["attempt"], plan["is_last"])]
            print("[%s] беру запасной вопрос про текущую ситуацию" % tag)
        variants = strong
        qual = strong_qual or qual
        plan = policy.finalize_reengagement(plan, variants, last_out)

    if plan["closed"]:
        print("[%s] диалог с лидом %s закрыт: %s"
              % (tag, lead_id, plan["closed_reason"]))

    with DB.get_conn() as c:
        c.execute(
            "INSERT INTO lead_messages "
            "(lead_id, direction, text, variants, qualification) VALUES (?,?,?,?,?)",
            (lead_id, "draft", plan.get("text") or (variants[0] if variants else ""),
             json.dumps(variants, ensure_ascii=False),
             json.dumps(qual, ensure_ascii=False)))
        c.execute("UPDATE leads SET qualification = ? WHERE id = ?",
                  (json.dumps(qual, ensure_ascii=False), lead_id))
        DB.set_dialog_state(c, lead_id, refusals=plan["refusals"],
                            closed=plan["closed"], reason=plan["closed_reason"])
        mode = DB.get_global_mode(c)

    if mode != "auto" or not plan.get("text"):
        return {"stored_only": False, "auto_sent": False, "mode": mode,
                "variants": variants, "qualification": qual, "plan": plan}

    await send(plan["text"])
    with DB.get_conn() as c:
        c.execute("INSERT INTO lead_messages (lead_id, direction, text) VALUES (?,?,?)",
                  (lead_id, "out", plan["text"]))
        c.execute("UPDATE lead_messages SET variants = NULL "
                  "WHERE lead_id = ? AND direction = 'draft'", (lead_id,))
    return {"stored_only": False, "auto_sent": True, "mode": mode,
            "variants": variants, "qualification": qual, "plan": plan}
