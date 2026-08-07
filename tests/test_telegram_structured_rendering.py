from pathlib import Path

from lingtai.mcp_servers.telegram.manager import TelegramManager
from lingtai.mcp_servers.telegram.render import render_structured_blocks
from tests._notification_store_helpers import FakeNotificationStore


class _Account:
    alias = "bot"

    def __init__(self):
        self.calls = []

    def send_message(self, chat_id, text, **kwargs):
        self.calls.append((chat_id, text, kwargs))
        return {"message_id": 1}

    def get_task_card(self, chat_id):
        return None

    def set_task_card(self, chat_id, message_id):
        pass

    def get_last_message_id(self, chat_id):
        return None


class _Service:
    def __init__(self, account):
        self.default_account = account

    def get_account(self, alias):
        return self.default_account

    def list_accounts(self):
        return [self.default_account.alias]

    def taskcard_enabled(self):
        return False


def test_structured_blocks_are_escaped_and_sent_as_html(tmp_path: Path):
    account = _Account()
    manager = TelegramManager(
        _Service(account),
        working_dir=tmp_path,
        on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )
    result = manager.handle({
        "action": "send",
        "chat_id": 123,
        "text": "ignored",
        "rendering_mode": "plain_text",
        "structured_blocks": [
            {"heading": "中文标题"},
            {"paragraph": "内容 & <b>raw</b>"},
            {"code_block": "x < 2"},
        ],
    })

    assert result["status"] == "sent"
    _, text, options = account.calls[-1]
    assert text == (
        "<b>中文标题</b>\n"
        "内容 &amp; &lt;b&gt;raw&lt;/b&gt;\n"
        "<pre>x &lt; 2</pre>"
    )
    assert options["parse_mode"] == "HTML"
    assert render_structured_blocks([{"bullet": "👀 & done"}]) == "• 👀 &amp; done"
