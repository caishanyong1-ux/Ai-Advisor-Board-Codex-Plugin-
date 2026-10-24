from __future__ import annotations

import fcntl
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .core import BoardError, VERSION, canonical, private_dir, state_home


def request(endpoint, path, value=None, timeout=120):
    conn = http.client.HTTPConnection("127.0.0.1", endpoint["port"], timeout=timeout)
    try:
        conn.request("POST" if value is not None else "GET", path, canonical(value).encode() if value is not None else None, {"Authorization": "Bearer " + endpoint["token"], "Content-Type": "application/json"})
        response = conn.getresponse()
        data = json.loads(response.read(16000001))
        if response.status != 200:
            raise BoardError(data.get("error", "Local request failed"))
        return data
    finally:
        conn.close()


def endpoint():
    home = private_dir(state_home())

    def existing():
        try:
            info = json.loads((home / "endpoint.json").read_text())
            health = request(info, "/health", timeout=1)
            if health["version"] != VERSION:
                raise BoardError("A different worker version is active. Finish/cancel its work before restarting the local worker.")
            return info
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            return None

    with open(home / "launch.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        info = existing()
        if info:
            return info
        root = Path(__file__).resolve().parent.parent
        with open(home / "worker.log", "ab") as log:
            os.chmod(home / "worker.log", 0o600)
            subprocess.Popen([sys.executable, "-m", "runtime.server"], cwd=str(root), stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, close_fds=True, env={**os.environ, "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        for _ in range(100):
            info = existing()
            if info:
                return info
            time.sleep(0.05)
        raise BoardError("Local worker could not start; inspect worker.log in the plugin's state directory")


def call(name, args):
    return request(endpoint(), "/rpc", {"name": name, "args": args})["result"]
