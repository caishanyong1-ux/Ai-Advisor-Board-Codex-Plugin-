#!/usr/bin/env python3
"""Install a checksum-pinned private gcloud CLI. No login or cloud mutations."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

from runtime.core import canonical, private_dir, state_home, write_private

SDK_VERSION = "590.0.0"
SDK_URL = "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-darwin-arm.tar.gz"
SDK_SHA256 = "b1f80ffe9cc970ff1935b4adfddd0fb89d7e7b3341a88d70216d4283211b8d2d"
SOURCE = "https://docs.cloud.google.com/sdk/docs/install-sdk"


def extract_verified(archive, destination):
    digest = hashlib.sha256()
    with open(archive, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != SDK_SHA256:
        raise RuntimeError("SDK checksum changed; verify the current official download before updating this helper")
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if any(not m.name.startswith("google-cloud-sdk/") for m in members):
            raise RuntimeError("Unexpected archive layout")
        bundle.extractall(destination, members=members, filter="data")
    sdk = Path(destination) / "google-cloud-sdk"
    if (sdk / "VERSION").read_text().strip() != SDK_VERSION:
        raise RuntimeError("Unexpected SDK version")
    return sdk


def verify(sdk, python, config):
    env = {**os.environ, "CLOUDSDK_PYTHON": str(python), "CLOUDSDK_CONFIG": str(config),
           "CLOUDSDK_CORE_DISABLE_USAGE_REPORTING": "true", "CLOUDSDK_CORE_DISABLE_PROMPTS": "1"}
    result = subprocess.run([str(sdk / "bin/gcloud"), "version", "--format=json"],
                            env=env, capture_output=True, text=True, timeout=45, check=True)
    versions = json.loads(result.stdout)
    if versions.get("Google Cloud SDK") != SDK_VERSION:
        raise RuntimeError("gcloud version does not match the reviewed package")
    return versions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=state_home())
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--archive", type=Path, help="Already downloaded official archive; the same SHA256 is required")
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        raise SystemExit("Run this installer with Python 3.12+ for safe tar extraction. The plugin itself supports Python 3.9+.")
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        raise SystemExit("This reviewed installer is for Apple Silicon macOS only")
    os.umask(0o077)
    home = private_dir(args.home.expanduser().resolve())
    folder = private_dir(home / "tools")
    config = private_dir(home / "gcloud")
    sdk = folder / "google-cloud-sdk"
    with open(folder / "install.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not sdk.exists():
            with tempfile.TemporaryDirectory(prefix="gcloud-", dir=folder) as temporary:
                temporary = Path(temporary)
                archive = args.archive or temporary / "sdk.tar.gz"
                if not args.archive:
                    print("Downloading the official Google Cloud CLI; no account or billing changes.", flush=True)
                    with urllib.request.urlopen(SDK_URL, timeout=90) as response, archive.open("wb") as output:
                        expected = int(response.headers.get("Content-Length", "0"))
                        total = 0
                        for block in iter(lambda: response.read(1024 * 1024), b""):
                            total += len(block)
                            if total > 100 * 1024 * 1024:
                                raise RuntimeError("Download exceeds the reviewed package size limit")
                            output.write(block)
                        if expected and total != expected:
                            raise RuntimeError("Download incomplete; retry the official download before installation")
                extracted = extract_verified(archive, temporary)
                verify(extracted, args.python, config)
                extracted.rename(sdk)
        versions = verify(sdk, args.python, config)
        write_private(folder / "runtime.json", canonical({"python": str(args.python.resolve())}))
        record = {"version": SDK_VERSION, "archive_sha256": SDK_SHA256, "source": SOURCE,
                  "python": str(args.python.resolve()), "versions": versions,
                  "validation": "gcloud version only; authentication and paid inference not tested"}
        write_private(folder / "install-record.json", canonical(record))
        print(json.dumps({"installed": str(sdk), "version": SDK_VERSION, "login_started": False}))


if __name__ == "__main__":
    main()
