from lingtai.kernel.prompt import SystemPromptManager
from lingtai.tools.registry import INTRINSICS as _TEST_INTRINSICS
from tests._workdir_lease_helpers import make_test_lease
from tests._snapshot_helpers import make_test_snapshot_port, make_test_source_revision_port
from tests._lifecycle_clock_helpers import make_test_lifecycle_clock
from tests._notification_store_helpers import notification_store_for
from tests._agent_presence_helpers import make_test_presence_store






def test_mail_send_passes_attachments(tmp_path):
    """Mail handler should pass attachments to mail service."""
    from lingtai.kernel.base_agent import BaseAgent
    from unittest.mock import MagicMock
    from pathlib import Path

    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"

    mail_svc = MagicMock()
    mail_svc.address = str(tmp_path / "test")
    mail_svc.send.return_value = None

    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=svc, agent_name="test", working_dir=tmp_path / "test", mail_service=mail_svc, workdir_lease=make_test_lease(), snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test"))

    # Create a real file to attach
    attachment = tmp_path / "file.png"
    attachment.write_bytes(b"PNG_DATA")

    # Call the email handler directly (mail intrinsic was renamed in 0.7.5).
    # ``email`` is a migrated LTP v2 family: the send arguments live in
    # ``send``'s own strict ``input`` object, not at the envelope root.
    result = agent._intrinsics["email"]({
        "action": "send",
        "input": {
            "address": str(tmp_path / "other"),
            "message": "here is a file",
            "attachments": [str(attachment)],
        },
    })
    assert result["status"] == "sent"
    # Delivery is async via mailman thread — wait for it
    import time
    time.sleep(0.5)
    # Verify attachments were passed through
    call_args = mail_svc.send.call_args
    sent_message = call_args[0][1]  # second positional arg is the message dict
    assert "attachments" in sent_message
    assert sent_message["attachments"] == [str(attachment)]
