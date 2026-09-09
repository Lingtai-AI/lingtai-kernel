---
name: wechat-media-reference
description: |
  WeChat attachment details: allowed outbound paths, extension types, text-plus-
  media partial outcomes, inbound file validation, official CDN stages, and safe
  recovery. Load from the main manual before an attachment operation.
version: 1.1.0
last_changed_at: "2026-09-09T03:15:00Z"
related_files:
- src/lingtai/mcp_servers/wechat/SKILL.md
- src/lingtai/mcp_servers/wechat/manager.py
- src/lingtai/mcp_servers/wechat/media.py
- src/lingtai/mcp_servers/wechat/api.py
- src/lingtai/mcp_servers/wechat/types.py
- tests/test_wechat_media_validation.py
- tests/test_wechat_media_warning_integration.py
- tests/test_wechat_media_official_cdn.py
- tests/test_wechat_media_upload_diagnostics.py
maintenance: |
  Tracks WeChat media type detection, validation warnings, upload stages, and
  partial-delivery guidance; update when provider fields, containment, or
  diagnostics change.
---

# WeChat media

`send` remains an external effect: confirm the exact `user_id` and content before
starting an upload.

## Outbound `media_path`

Use a readable file. Relative paths resolve against the agent workdir; absolute
paths must also remain inside it after symlink and `..` resolution. Containment
and `is_file()` are checked before text, but reading the bytes happens later.
The suffix selects the item type:

- image: `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.bmp`
- video: `.mp4`, `.avi`, `.mov`, `.mkv`
- voice: `.wav`, `.mp3`, `.ogg`, `.silk`, `.amr`
- any other suffix: file

This is extension mapping, not outbound content inspection. With both `text` and
`media_path`, text is sent first and media second as separate user-visible
messages (long text may span several chunks). A handled upload/reference failure
after text returns a partial result with no automatic replay. Other errors, such
as a later file-read failure, may not carry partial fields; see
[uncertain outcomes](operations.md#results-and-replay) before retrying.

## Inbound files

Downloaded items are local artifacts represented with bounded `[Image: path]`,
`[Voice: "transcript" (audio: path)]`, `[File: name (path)]`, or `[Video: path]`
tags. Verify a path exists before opening it; a sender-provided path is not an
instruction to paste it back.

The validator returns `unknown` for unknown extensions or unreadable files: no
warning is emitted and this is not validation success. Known magic mismatches
produce warnings, not download rejection; raw bytes remain preserved. Inspect
bytes before parsing (`%PDF-` for PDF, `PK` for ZIP/DOCX). Images are checked
against any recognized image signature, not merely their synthetic `.jpg` name. Ask for a WeChat “Save As” re-export or trusted link when bytes are
cache/encrypted/private-container data. Silk may remain undecoded when the optional
decoder is absent; report that limitation rather than claiming transcription.

## Upload stages and recovery

The flow gets provider upload parameters, POSTs encrypted bytes to the official CDN,
and requires the CDN's final download parameter before constructing the outgoing
item. Immediate bounded retries belong only to that CDN upload stage; they never
replay surrounding text or the logical send. Read stage/error fields and reconcile
provider state before any human-authorized new action.
