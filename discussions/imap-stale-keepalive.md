# IMAP Stale Connection Bug & Proposed Keepalive Fix

See the PR description for the full analysis.

## TL;DR

MCP IMAP connections to Gmail go stale during WSL sleep because:
1. Gmail drops idle connections after ~30 minutes
2. TCP keepalive defaults (7200s on both Linux and macOS) are far too long to detect this
3. No socket timeout is set on the IMAP connection

Fix: application-layer keepalive in the kernel's MCP manager.
