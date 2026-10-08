from __future__ import annotations

from dorj_agent_cli import main


def test_pending_confirmation_actions_finds_unobserved_calls():
    action = {
        "kind": "ActionEvent",
        "source": "agent",
        "tool_name": "execute_bash",
        "tool_call_id": "call-2",
        "action": {"kind": "ExecuteBashAction", "command": "rm -rf build"},
    }
    observed_action = {
        **action,
        "tool_call_id": "call-1",
    }
    observation = {
        "kind": "ObservationEvent",
        "source": "environment",
        "tool_call_id": "call-1",
    }
    message = {"kind": "MessageEvent", "source": "agent"}

    pending = main._pending_confirmation_actions(
        [message, observed_action, observation, action]
    )

    assert pending == [action]


def test_confirmation_preview_prefers_the_command():
    event = {
        "action": {"kind": "ExecuteBashAction", "command": "rm -rf build"},
        "summary": "Remove build",
    }

    assert main._confirmation_preview(event) == "rm -rf build"


def test_run_and_chat_accept_yes():
    parser = main.build_parser()
    run = parser.parse_args(["run", "task", "--yes"])
    chat = parser.parse_args(["chat", "--yes"])

    assert run.yes is True
    assert chat.yes is True
    assert main.EXIT_CONFIRMATION_REJECTED == 5
