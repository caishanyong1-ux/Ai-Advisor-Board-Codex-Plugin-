#!/usr/bin/env python3
"""Install this reviewed local package without modifying existing plugins."""
from datetime import datetime
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
NAME = "ai-advisory-board"
FILES = ["edition.json", "plugin.json", "mcp.json", ".codex-plugin", ".mcp.json", "runtime", "mcp", "skills", "licenses", "prepare_vertex.py", "README.md", "NOTICE.md", "LICENSE", "VALIDATION.json"]


def main():
    home = Path.home()
    marketplace = home / ".agents/plugins/marketplace.json"
    parent = marketplace.parent / "plugins"
    parent.mkdir(parents=True, exist_ok=True)
    source = parent / NAME
    staging = parent / ("." + NAME + "-staging-" + secrets.token_hex(6))
    staging.mkdir()
    try:
        for item in FILES:
            current = ROOT / item
            if current.is_dir():
                shutil.copytree(current, staging / item, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
            else:
                shutil.copy2(current, staging / item)
        data = json.loads(marketplace.read_text()) if marketplace.exists() else {"name": "personal", "interface": {"displayName": "Personal"}, "plugins": []}
        if data.get("name") != "personal":
            raise RuntimeError("The existing marketplace has a different name; refusing to overwrite it")
        entries = [item for item in data.get("plugins", []) if item.get("name") == NAME]
        desired = {"name": NAME, "source": {"source": "local", "path": "./plugins/" + NAME}, "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}
        if entries and any(item.get("source") != desired["source"] for item in entries):
            raise RuntimeError("An unrelated plugin already uses this name; refusing to replace it")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        backup_dir = marketplace.parent / "backups" / (NAME + "-" + stamp)
        backup_dir.mkdir(parents=True, mode=0o700)
        if marketplace.exists():
            shutil.copy2(marketplace, backup_dir / "marketplace.json")
        if source.exists():
            source.rename(backup_dir / NAME)
        staging.rename(source)
        if not entries:
            data.setdefault("plugins", []).append(desired)
        tmp = marketplace.with_name(".marketplace-" + secrets.token_hex(6) + ".json")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, marketplace)
        codex = shutil.which("codex")
        if not codex:
            raise RuntimeError("Codex CLI not found; source package is registered but not installed")
        result = subprocess.run([codex, "plugin", "add", NAME + "@personal", "--json"], text=True, capture_output=True)
        print(json.dumps({"source": str(source), "backup": str(backup_dir), "cli_exit_code": result.returncode}, ensure_ascii=False))
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        if result.returncode:
            raise SystemExit(result.returncode)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


if __name__ == "__main__":
    main()
