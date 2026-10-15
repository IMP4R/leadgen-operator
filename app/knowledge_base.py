import os
from app import config

KB_FILES = [
    "offer.md",
    "qualification.md",
    "script.md",
    "script_objections.md",
    "qa.md",
    "reglament.md",
]

def load_kb() -> dict:
    kb = {}
    for name in KB_FILES:
        path = os.path.join(config.KB_DIR, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                kb[name] = f.read()
    return kb

def kb_text() -> str:
    parts = []
    for name, text in load_kb().items():
        parts.append("=== %s ===\n%s" % (name, text.strip()))
    return "\n\n".join(parts)
