"""Tests for _ensure_first_user_message in the Zhipu GLM adapter.

These verify that the function correctly injects a leading user message
when the wire starts with an assistant role message (which causes GLM
HTTP 400 error 1214 after molt/reconstruction).
"""

from lingtai.llm.zhipu.adapter import _ensure_first_user_message


class TestEnsureFirstUserMessage:
    def test_already_starts_with_user(self):
        """No injection needed when first non-system message is user."""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = _ensure_first_user_message(msgs)
        assert result == msgs
        assert len(result) == 3

    def test_starts_with_assistant_after_system(self):
        """Injects user message after system when first non-system is assistant (post-molt scenario)."""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "I am the summary."},
            {"role": "user", "content": "Continue working."},
        ]
        result = _ensure_first_user_message(msgs)
        assert len(result) == 4
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "sys"
        assert result[1]["role"] == "user"
        assert result[1]["content"] == "Continue."
        assert result[2]["role"] == "assistant"
        assert result[3]["role"] == "user"

    def test_starts_with_assistant_no_system(self):
        """Injects user message when very first message is assistant."""
        msgs = [
            {"role": "assistant", "content": "Summary of previous work."},
            {"role": "user", "content": "Next task."},
        ]
        result = _ensure_first_user_message(msgs)
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Continue."
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "user"

    def test_only_system_messages(self):
        """No injection when only system messages exist (nothing to do)."""
        msgs = [
            {"role": "system", "content": "sys1"},
            {"role": "system", "content": "sys2"},
        ]
        result = _ensure_first_user_message(msgs)
        assert result == msgs
        assert len(result) == 2

    def test_empty_list(self):
        """No injection on empty list."""
        result = _ensure_first_user_message([])
        assert result == []

    def test_starts_with_tool(self):
        """Injects when first non-system message is tool role."""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "tool", "content": "result"},
        ]
        result = _ensure_first_user_message(msgs)
        assert len(result) == 3
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "tool"

    def test_idempotent(self):
        """Running twice produces the same result as running once."""
        msgs = [
            {"role": "assistant", "content": "summary"},
            {"role": "user", "content": "task"},
        ]
        once = _ensure_first_user_message(msgs)
        twice = _ensure_first_user_message(once)
        assert once == twice

    def test_no_system_assistant_first(self):
        """Direct assistant start without system prefix."""
        msgs = [{"role": "assistant", "content": "summary"}]
        result = _ensure_first_user_message(msgs)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
