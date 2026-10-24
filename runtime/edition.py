"""Edition defaults are package data, never an export of local preferences/secrets."""
import json
from pathlib import Path


def settings():
    path = Path(__file__).resolve().parent.parent / 'edition.json'
    return json.loads(path.read_text()) if path.exists() else {'edition': 'public', 'excluded_publishers': [], 'reuse_grok_connection': False}
