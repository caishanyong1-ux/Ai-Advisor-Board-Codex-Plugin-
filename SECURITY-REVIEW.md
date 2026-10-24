# Public release review: 1.3.3

Reviewed on October 24, 2026. No blocking security or privacy findings were identified in the reviewed public package. This is a source, packaging, and regression-test review, not a guarantee that all vulnerabilities have been excluded.

## Release integrity

- Public archive: `ai-advisory-board-1.3.3-public.zip` (291,519 bytes).
- SHA-256: `f9a64419872dea95bb4f3c3163eff023e031e4ba49c6160027fc5cb064965b3e`.
- All 79 packaged files match the extracted tree. The 78 entries in `RELEASE-MANIFEST.json` match their SHA-256 hashes; the manifest itself is the remaining file.
- Archive CRC, duplicate names, path traversal, symlinks, unexpected binary files, and generated/private-state file checks passed.
- Packaged runtime files match the release source. Public defaults retain supported Claude routes and disable automatic reuse of a personal Grok connection.
- Both plugin manifests declare MIT. The installer includes `LICENSE`; original upstream copyright and MIT notices remain intact in `licenses/ai-advisory-board-MIT.txt`.

## Privacy checks

The public tree and ZIP were scanned for common provider/GitHub/cloud token formats, private keys, JWTs, credentials in URLs, personal home paths, personal attribution, email-like strings, suspicious opaque Python string literals, and private state/database/log files. No real credentials, personal contact details, account configuration, meeting records, or local backups were found in the package.

Manual review resolved the scanner's remaining candidates: dynamic evaluation occurs only in tests against the bundled, pinned upstream fixture; an email-like match is a synthetic URL used to test rejection of URL credentials.

## Security checks

Source review covered the loopback HTTP service, bearer authentication, exact Host/Origin checks, approval-ticket validation, replay and plan-integrity protections, HTML escaping and content-security policy, owner-only credential/state storage, credential redaction, source-path restrictions, project/conversation boundaries, HTTPS provider connections, and refusal to follow provider redirects or silently retry paid calls.

The 1.3.3 review additionally checked conversation-scoped language preferences, translation-template completeness, fixed HTML/form/link structure, ordered placeholders, and preservation of approved plans and original answers when presentation language changes.

## Regression validation

`python3 -B -m unittest discover -s tests -v`

Result: **211 tests passed** in 26.614 seconds on Python 3.9 with isolated temporary state, synthetic providers, and localhost HTTP servers. No paid inference calls were made. Tests cover authentication/origin rejection, HTML escaping, credential exclusion/redaction, project separation, approval replay/tampering, bounded source capture, recovery, and language isolation.

## Boundaries

Users connect their own providers. Approved prompts and selected source snapshots are sent to the approved services; provider retention policies still apply. The supported Gemini background flow discloses cloud storage and cleanup status. Local private state uses filesystem permissions, not encryption or an OS sandbox against other processes under the same account. Live-provider behavior and translation quality are not established by synthetic tests.

The repository's pre-existing Git history retains its existing author metadata. This review does not rewrite that history. Private development records and raw local test logs are excluded from publication.
