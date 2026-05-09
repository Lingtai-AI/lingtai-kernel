"""Claude Code Avatar Wrapper — persistent process that bridges
Claude Code sessions with the lingtai agent network.

Responsibilities:
1. Heartbeat: writes .agent.heartbeat every 1s
2. Signal handling: watches for .suspend, .sleep, .prompt
3. Mail polling: checks mailbox/inbox/ for new messages
4. Claude Code session: manages --session-id / --resume lifecycle
5. .agent.json: updates state field

Usage: python cc_avatar_wrapper.py <working_dir>
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

HEARTBEAT_INTERVAL = 1.0
MAIL_POLL_INTERVAL = 2.0
CLAUDE_BIN = "claude"


class ClaudeCodeAvatarProcess:
    def __init__(self, working_dir: str):
        self.wd = Path(working_dir)
        self.session_id = str(uuid.uuid4())
        self.running = True
        self.state = "active"
        self._seen_mail: set[str] = set()
        self._first_turn_done = False

        # Scan existing inbox to mark as seen (don't replay old mail)
        inbox = self.wd / "mailbox" / "inbox"
        if inbox.is_dir():
            for d in inbox.iterdir():
                if d.is_dir():
                    self._seen_mail.add(d.name)

    def run(self):
        """Main loop — heartbeat + signal check + mail poll."""
        # Write initial heartbeat
        self._write_heartbeat()

        # Process .prompt file (mission / first prompt)
        first_prompt = self._consume_prompt()
        if first_prompt:
            self._send_to_claude(first_prompt, is_first=True)

        last_heartbeat = time.time()
        last_mail_check = time.time()

        while self.running:
            now = time.time()

            # Heartbeat
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                self._write_heartbeat()
                last_heartbeat = now

            # Signal files
            self._check_signals()

            # Mail polling
            if now - last_mail_check >= MAIL_POLL_INTERVAL:
                self._check_mail()
                last_mail_check = now

            time.sleep(0.5)

        self._update_agent_json("suspended")

    def _consume_prompt(self) -> str | None:
        """Read and delete the .prompt file if it exists."""
        prompt_file = self.wd / ".prompt"
        if prompt_file.is_file():
            content = prompt_file.read_text(encoding="utf-8").strip()
            prompt_file.unlink(missing_ok=True)
            return content if content else None
        return None

    def _write_heartbeat(self):
        hb = self.wd / ".agent.heartbeat"
        hb.write_text(str(time.time()))

    def _update_agent_json(self, state: str):
        aj = self.wd / ".agent.json"
        if aj.is_file():
            try:
                data = json.loads(aj.read_text())
                data["state"] = state
                aj.write_text(json.dumps(data, indent=2))
            except (json.JSONDecodeError, OSError):
                pass

    def _check_signals(self):
        # .suspend -> graceful shutdown
        if (self.wd / ".suspend").is_file():
            self.running = False
            (self.wd / ".suspend").unlink(missing_ok=True)
            return

        # .sleep -> go idle (stop processing but keep heartbeat)
        if (self.wd / ".sleep").is_file():
            self.state = "asleep"
            self._update_agent_json("asleep")
            (self.wd / ".sleep").unlink(missing_ok=True)
            # Sleep loop — heartbeat only, check for wake signals
            while self.running and self.state == "asleep":
                self._write_heartbeat()
                if (self.wd / ".suspend").is_file():
                    self.running = False
                    (self.wd / ".suspend").unlink(missing_ok=True)
                    return
                # Wake on new mail
                if self._has_new_mail():
                    self.state = "active"
                    self._update_agent_json("active")
                    break
                time.sleep(1.0)
            return

        # .prompt -> inject message
        prompt_file = self.wd / ".prompt"
        if prompt_file.is_file():
            content = prompt_file.read_text(encoding="utf-8").strip()
            prompt_file.unlink(missing_ok=True)
            if content:
                self._send_to_claude(content, is_first=not self._first_turn_done)

    def _has_new_mail(self) -> bool:
        inbox = self.wd / "mailbox" / "inbox"
        if not inbox.is_dir():
            return False
        for d in inbox.iterdir():
            if d.is_dir() and d.name not in self._seen_mail:
                return True
        return False

    def _check_mail(self):
        inbox = self.wd / "mailbox" / "inbox"
        if not inbox.is_dir():
            return
        for d in sorted(inbox.iterdir()):
            if not d.is_dir() or d.name in self._seen_mail:
                continue
            self._seen_mail.add(d.name)
            msg_file = d / "message.json"
            if msg_file.is_file():
                try:
                    msg = json.loads(msg_file.read_text())
                    sender = msg.get("from", "unknown")
                    subject = msg.get("subject", "")
                    body = msg.get("message", msg.get("body", ""))
                    prompt = (
                        f"[EMAIL RECEIVED]\n"
                        f"From: {sender}\n"
                        f"Subject: {subject}\n\n"
                        f"{body}\n\n"
                        f"Reply using the email sending instructions in CLAUDE.md if needed."
                    )
                    self._send_to_claude(prompt, is_first=not self._first_turn_done)
                except (json.JSONDecodeError, OSError):
                    pass

    def _send_to_claude(self, message: str, is_first: bool = False):
        cmd = [
            CLAUDE_BIN,
            "--print",
            "--dangerously-skip-permissions",
            "--output-format", "text",
        ]

        if is_first and not self._first_turn_done:
            cmd += ["--session-id", self.session_id]
            self._first_turn_done = True
        else:
            cmd += ["--resume", self.session_id]

        cmd.append(message)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(self.wd),
                timeout=600,
            )
            if result.returncode == 0:
                self._log("claude_response", output=result.stdout[:500])
            else:
                self._log("claude_error", stderr=result.stderr[:500])
        except subprocess.TimeoutExpired:
            self._log("claude_timeout")
        except FileNotFoundError:
            self._log("claude_not_found")
            self.running = False

    def _log(self, event: str, **kwargs):
        entry = {"ts": time.time(), "event": event, **kwargs}
        log_path = self.wd / "logs" / "avatar.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <working_dir>", file=sys.stderr)
        sys.exit(1)

    working_dir = sys.argv[1]
    avatar = ClaudeCodeAvatarProcess(working_dir)

    # Handle SIGTERM gracefully
    def on_sigterm(signum, frame):
        avatar.running = False
    signal.signal(signal.SIGTERM, on_sigterm)
    signal.signal(signal.SIGINT, on_sigterm)

    avatar.run()


if __name__ == "__main__":
    main()
