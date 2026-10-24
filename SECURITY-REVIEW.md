# Public release review: 1.3.4

Reviewed on October 24, 2026. No blocking security or privacy findings were identified in the reviewed public package. This is a source, packaging, and regression-test review, not a guarantee that all vulnerabilities have been excluded.

## Release integrity

- Public archive: `ai-advisory-board-1.3.4-public.zip` (309,018 bytes).
- SHA-256: `8612e375bc5877ae71ab16603176cc6f4bfaa7725adaf00b4862825ce79ecb6b`.
- All 85 packaged files match the extracted tree. The 84 entries in `RELEASE-MANIFEST.json` match their SHA-256 hashes; the manifest itself is the remaining file.
- Archive CRC, duplicate names, path traversal, symlinks, unexpected binary files, and generated/private-state file checks passed.
- Packaged runtime files match the release source. Public defaults retain supported Claude routes and disable automatic reuse of a personal Grok connection.
- Both plugin manifests declare MIT. Original upstream copyright and MIT notices remain intact in `licenses/ai-advisory-board-MIT.txt`.

## Privacy checks

The public tree and ZIP were scanned for common provider/GitHub/cloud token formats, private keys, JWTs, credentials in URLs, personal home paths, personal attribution, email-like strings, suspicious opaque Python string literals, and private state/database/log files. No real credentials, personal contact details, account configuration, meeting records, or local backups were found in the package.

Manual review resolved two path-pattern scanner matches in `runtime/http_errors.py`: they are generic patterns used to remove home paths from provider errors, not personal paths. Email-like matches in tests are synthetic rejection/redaction fixtures. Dynamic evaluation occurs only in tests against the bundled, pinned upstream fixture.

## Security checks

The release delta was reviewed against the published 1.3.3 package, including all changed runtime modules and the three new modules. The existing loopback binding, bearer authentication, exact Host/Origin checks, approval replay protections, owner-only storage, source-path restrictions, HTTPS enforcement, and refusal to follow provider redirects remain in place and are covered by regression tests.

The 1.3.4 review additionally checked:

- Bounded provider-error reads, fixed diagnostic vocabulary, credential/address/request-content filtering, hashed request IDs, and omission of raw bodies and arbitrary error metadata.
- Shared-context output limits computed from complete wire inputs, preserved requested maxima and reasoning settings, and rejection before dispatch when no verified output headroom remains.
- Disclosure of the capacity policy for both external members and external chairmen, with recorded effective limits and no automatic paid retry.
- Explicit reapproval restricted to the audited 1.3.3 first round at rest before review; original approval, completed work, and costs are retained. Changed attempts, review packets, or runtime contracts invalidate recovery.
- Agent identity scoped from the original saved conversation binding. Session rebinding cannot relabel identities, and same-conversation reuse remains blocked across stages and agendas.

## Regression validation

`python3 -B -m unittest discover -s tests -v`

Result: **227 tests passed** in 27.912 seconds on Python 3.9, run from the exact public package with isolated temporary state, synthetic providers, and localhost HTTP servers. No paid inference calls were made. Tests cover authentication/origin rejection, HTML escaping, credential exclusion/redaction, project separation, approval replay/tampering, bounded source capture, recovery, language isolation, and the new capacity/diagnostic/identity behavior.

## Boundaries

Users connect their own providers. Approved prompts and selected source snapshots are sent to the approved services; provider retention policies still apply. Local private state uses filesystem permissions, not encryption or an OS sandbox against other processes under the same account. Live-provider acceptance is not established by synthetic tests. The capacity calculation is a conservative UTF-8 estimate plus framing, not an exact tokenizer; the original discarded HTTP 400 response cannot be reconstructed. Host tools must still supply truthful agent identities.

The repository's pre-existing Git history retains its existing author metadata. This review does not rewrite that history. Private development records and raw local test logs are excluded from publication.
