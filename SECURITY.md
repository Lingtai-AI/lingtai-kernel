---
related_files:
- CONTRIBUTING.md
- README.md
maintenance: |
  Tracks the vulnerability-reporting process; update it if the private disclosure channel, GitHub Security Advisory availability, or required report contents change.
---

# Security Policy

## Reporting a vulnerability

Please do **not** disclose security-sensitive details in a public issue.

Preferred reporting path:

1. Use GitHub's private vulnerability reporting / Security Advisory flow for this
   repository if it is available.
2. If private reporting is not available, contact a maintainer directly and keep
   details minimal until a private channel is established.

Include enough information for maintainers to reproduce and assess the issue:

- affected component or command
- LingTai / Python / OS versions when relevant
- minimal reproduction steps
- expected impact
- whether secrets, tokens, local files, or external services may be exposed

## Scope

Security issues include credential leaks, unintended filesystem access, unsafe
external side effects, privilege bypasses, prompt/tool-channel injection hazards,
and vulnerabilities in bundled runtime components.
