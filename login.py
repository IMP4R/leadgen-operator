"""One-time Telegram login via Kurigram (import name: pyrogram).
Run in a VISIBLE terminal - it asks for the login code interactively.
Creates leadgen.session next to this file; the bot reuses it later.
"""
import os
from dotenv import load_dotenv
from pyrogram import Client

load_dotenv()

api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]
phone = os.environ.get("PHONE") or None

app = Client(
    "leadgen",
    api_id=api_id,
    api_hash=api_hash,
    phone_number=phone,
    workdir=os.path.dirname(os.path.abspath(__file__)),
)

with app:
    me = app.get_me()
    print(f"Logged in as: {me.first_name} (id {me.id})")
    print("Session saved to leadgen.session - window can be closed.")
