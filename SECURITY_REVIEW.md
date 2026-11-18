# Security review

This page records the security evidence for the application source in commit
`48c84bdb75a2d4a612e85eb188498dc8f412ba2a`, reviewed on 14 November 2026.
It is evidence, not a certification of every operator deployment.

## Result

Two independent source reviews and a final independent low-risk recheck found
no open critical, high or medium issue in the reviewed source. The final two
low-risk changes add the same pre-authentication rate limit to revocation as to
token exchange and validate/cap Odoo search ordering.

Automated evidence on that exact source:

- Offline suite: **47/47 passed**; four configured live-service tests skipped.
- `pip-audit` against the exact hash-locked dependencies: **No known vulnerabilities**.
- Semgrep Python and secrets rules: **0 findings**.
- gitleaks across the complete three-commit history: **0 leaks**.
- The dependency lock contains hashes; the container installs with
  `--require-hashes --no-deps` and runs as uid 10001.

## Boundaries checked

- OAuth 2.1 authorization, dynamic client metadata and redirect addresses are
  validated; tokens are not passed through to Odoo.
- A person can connect only to a public HTTPS Odoo host that passes the SSRF
  checks and the optional exact-host allow-list.
- Read/write scopes, per-account policy, request bounds, timeouts and rate
  limits are enforced by the connector, not by assistant instructions.
- Keys are encrypted at rest with a connection key wrapped by the person's
  live authorization. Logs and error pages do not contain keys.
- Document extraction and other hostile-data paths are size/time bounded.
- Search models, fields, contexts, ordering and record access are constrained;
  the connector does not provide an unrestricted Odoo console.

## What operators still own

Terminate TLS at a maintained reverse proxy; expose only the public HTTPS
address; keep the container and host patched; back up and protect the state
volume; set `allowed_servers` when the service is for a fixed set of Odoo
hosts; and inspect updates before rebuilding. The review did not certify a
particular proxy, firewall, host or assistant subscription.

Report a suspected problem privately as described in [SECURITY.md](SECURITY.md).
