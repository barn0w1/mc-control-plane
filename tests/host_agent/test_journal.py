from pathlib import Path

import pytest
from mccp_host_agent.journal import CommandJournal


def test_journal_replays_saved_result_and_reports_once(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    journal = CommandJournal(path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert journal.receive("command-1", "digest-1") is None
    result = {
        "command_id": "command-1",
        "state": "succeeded",
        "error_code": None,
        "message": None,
        "observation": {"fixture": "active"},
    }
    journal.complete("command-1", result)

    restarted = CommandJournal(path)
    assert restarted.receive("command-1", "digest-1").value == result  # type: ignore[union-attr]
    assert [item.command_id for item in restarted.unreported()] == ["command-1"]
    restarted.mark_reported(["command-1"])
    assert restarted.unreported() == []


def test_journal_rejects_reused_command_id_with_changed_content(tmp_path: Path) -> None:
    journal = CommandJournal(tmp_path / "journal.db")
    journal.receive("command-1", "digest-1")
    with pytest.raises(ValueError, match="reused"):
        journal.receive("command-1", "digest-2")
