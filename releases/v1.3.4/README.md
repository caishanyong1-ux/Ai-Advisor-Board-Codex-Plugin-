# AI Advisory Board 1.3.4 public package

This directory contains the verified public Codex plugin ZIP and its SHA-256 checksum. The package uses MIT licensing and preserves the upstream MIT notice.

Changes include shared-context output capacity for Vertex chat routes, bounded and sanitized HTTP diagnostics, explicitly approved recovery of eligible 1.3.3 first rounds, and conversation-scoped Codex agent identities. See the root README for details.

Before installation, verify the archive with `shasum -a 256 -c SHA256SUMS` from this directory. Extract the ZIP and follow the installation instructions in the included `README.md`.

The exact public package passed 227 offline regression tests. See the root `SECURITY-REVIEW.md` for the review scope and limitations. No credentials, account configuration, meeting records, or private development records are included.
