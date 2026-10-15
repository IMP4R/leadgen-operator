"""YetReply API client — replaces Telegram (pyrogram) layer.

Uses YetReply REST API + WebSocket for dialog management.
Auth: POST /api/login {username, password} -> {access_token, user}
"""
import os
import json
import asyncio
import urllib.parse

import httpx
import websockets

from app import config

API_BASE = os.getenv("YETREPLY_BASE_URL", "https://yetreply.ru") + "/api"


def _enc(s) -> str:
    """encodeURIComponent equivalent."""
    return urllib.parse.quote(str(s if s is not None else ""), safe="")


class YetReplyClient:
    """Async client for YetReply API."""

    def __init__(self, token=None, username=None, password=None):
        self.token = token or getattr(config, "YETREPLY_TOKEN", "")
        self.username = username or getattr(config, "YETREPLY_USERNAME", "")
        self.password = password or getattr(config, "YETREPLY_PASSWORD", "")
        self.account_id = None
        self.user = None

    async def login(self):
        """POST /api/login -> store token, return user info."""
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{API_BASE}/login",
                json={"username": self.username, "password": self.password},
            )
            r.raise_for_status()
            data = r.json()
        self.token = data["access_token"]
        self.user = data.get("user")
        return data

    @property
    def headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def _get(self, path, params=None):
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{API_BASE}{path}", headers=self.headers, params=params)
            r.raise_for_status()
            return r.json()

    async def _post(self, path, body=None):
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{API_BASE}{path}", headers=self.headers, json=body)
            r.raise_for_status()
            return r.json()

    async def _patch(self, path, body=None):
        async with httpx.AsyncClient() as c:
            r = await c.patch(f"{API_BASE}{path}", headers=self.headers, json=body)
            r.raise_for_status()
            return r.json()

    # ── Accounts ──

    async def get_accounts(self):
        return await self._get("/accounts")

    async def get_first_active_account_id(self):
        if self.account_id:
            return self.account_id
        accounts = await self.get_accounts()
        for a in accounts:
            if a.get("is_active"):
                self.account_id = a["id"]
                return self.account_id
        if accounts:
            self.account_id = accounts[0]["id"]
            return self.account_id
        raise RuntimeError("No YetReply accounts found")

    # ── Dialogs ──

    async def get_dialogs(self):
        return await self._get("/dialogs")

    async def get_messages(self, chat_id, account_id=None, limit=100):
        if account_id is None:
            account_id = await self.get_first_active_account_id()
        return await self._get(
            f"/dialogs/{_enc(chat_id)}/messages",
            params={"account_id": account_id, "limit": limit},
        )

    async def send_message(self, chat_id, text, account_id=None):
        if account_id is None:
            account_id = await self.get_first_active_account_id()
        return await self._post(
            f"/dialogs/{_enc(chat_id)}/send",
            {"account_id": account_id, "text": text},
        )

    async def check_replies(self, chat_id, account_id=None):
        if account_id is None:
            account_id = await self.get_first_active_account_id()
        return await self._get(
            f"/dialogs/{_enc(chat_id)}/check-replies",
            params={"account_id": account_id},
        )

    async def mark_read(self, chat_id, account_id, read_replies_count):
        return await self._post(
            f"/dialogs/{_enc(chat_id)}/mark-read",
            {"account_id": account_id, "read_replies_count": read_replies_count},
        )

    # ── WebSocket ──

    async def ws_listen(self, on_activity):
        """Connect to YetReply WebSocket.

        Calls on_activity(chat_id, account_id) on 'dialog_activity' events.
        Reconnects with exponential backoff.
        """
        ws_base = API_BASE.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_base}/ws?token={urllib.parse.quote(self.token)}"
        retry = 0
        while True:
            try:
                async with websockets.connect(ws_url) as ws:
                    print("[yr] WebSocket connected")
                    retry = 0

                    async def ping_loop():
                        while True:
                            await asyncio.sleep(25)
                            try:
                                await ws.send("ping")
                            except Exception:
                                break

                    ping_task = asyncio.create_task(ping_loop())
                    try:
                        async for raw in ws:
                            try:
                                data = json.loads(raw)
                                if data.get("type") == "dialog_activity":
                                    chat_id = data.get("chat_id")
                                    account_id = data.get("account_id")
                                    if chat_id:
                                        await on_activity(chat_id, account_id)
                            except (json.JSONDecodeError, TypeError):
                                pass
                    finally:
                        ping_task.cancel()
            except Exception as e:
                print(f"[yr] WebSocket error: {e}")
                delay = min(15, 2 ** min(retry, 4))
                retry += 1
                await asyncio.sleep(delay)
