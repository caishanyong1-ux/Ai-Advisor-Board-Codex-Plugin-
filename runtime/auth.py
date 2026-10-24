"""Google ADC via the official gcloud CLI. Tokens never leave this module's callers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

from .core import BoardError, canonical, private_dir, write_private


def project_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", value):
        raise BoardError("Enter a Google Cloud project ID (not its display name or billing account)")
    return value


class ADC:
    def __init__(self, home):
        self.home = Path(home)
        self.lock = threading.Lock()
        self.cached = None
        self.login_process = None

    def settings(self):
        path = self.home / "vertex-config.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def configure(self, project, quota_project=None):
        value = {"project_id": project_id(project), "quota_project_id": project_id(quota_project or project), "location": "global"}
        write_private(self.home / "vertex-config.json", canonical(value))
        self.cached = None
        return value

    def command(self):
        local = self.home / "tools/google-cloud-sdk/bin/gcloud"
        binary = str(local) if local.is_file() else shutil.which("gcloud")
        if not binary:
            raise BoardError("Google Cloud CLI is not installed. Run the plugin's prepare_vertex.py setup helper")
        env = {**os.environ, "CLOUDSDK_CONFIG": str(private_dir(self.home / "gcloud")),
               "CLOUDSDK_CORE_DISABLE_PROMPTS": "1", "CLOUDSDK_CORE_DISABLE_USAGE_REPORTING": "true"}
        runtime = self.home / "tools/runtime.json"
        if runtime.exists():
            python = json.loads(runtime.read_text()).get("python")
            if python and Path(python).is_file():
                env["CLOUDSDK_PYTHON"] = python
        return binary, env

    def status(self):
        try:
            self.command()
            installed = True
        except BoardError:
            installed = False
        adc_path = self.home / "gcloud/application_default_credentials.json"
        process = self.login_process
        return {"gcloud_installed": installed, "adc_file_present": adc_path.is_file(),
                "login_state": "running" if process and process.poll() is None else ("finished" if process and process.returncode == 0 else ("failed" if process else "not_started")),
                "configuration": self.settings(), "notice": "File presence is not authentication, billing, IAM or model availability verification."}

    def login(self):
        with self.lock:
            if self.login_process and self.login_process.poll() is None:
                return
            binary, env = self.command()
            # Initiated only by the human's local setup button. No token or OAuth URL is returned by MCP.
            self.login_process = subprocess.Popen([binary, "auth", "application-default", "login", "--disable-quota-project", "--quiet"],
                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.cached = None

    def token(self):
        with self.lock:
            if self.cached and self.cached[1] > time.monotonic():
                return self.cached[0]
            binary, env = self.command()
            try:
                result = subprocess.run([binary, "auth", "application-default", "print-access-token", "--quiet"],
                    env=env, capture_output=True, text=True, timeout=35)
            except (OSError, subprocess.TimeoutExpired):
                raise BoardError("Google ADC token refresh failed; use the local setup page to sign in again") from None
            value = result.stdout.strip()
            if result.returncode or not value or any(c.isspace() for c in value) or len(value) > 16000:
                raise BoardError("Google ADC is not ready; complete Google sign-in on the setup page")
            self.cached = (value, time.monotonic() + 180)
            return value
