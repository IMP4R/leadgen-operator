"""Telegram layer (Kurigram, import name pyrogram).

Listens to incoming DMs from leads, stores them in db,
asks DialogEngine for suggestions, sends replies per panel mode.
Requires leadgen.session in project root (created by login.py).
"""
from pyrogram import Client
from app import config

def make_client() -> Client:
    return Client(
        config.SESSION_NAME,
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        workdir=config.BASE_DIR,
    )

# TODO: message handlers, lead routing, send queue
