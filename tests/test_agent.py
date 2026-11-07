"""End-to-end agent loop tests using fakes (no network, no real model)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage

from shoppi.agent.build import build_agent
from tests.fakes import CannedDocs, InMemoryCalendar, ScriptedChatModel

LA = ZoneInfo("America/Los_Angeles")
CFG = {"configurable": {"thread_id": "t"}}


def call(name: str, args: dict, id: str = "c1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id}])


def run(script, cal=None, docs=None, text="hi"):
    model = ScriptedChatModel(script=script, seen=[])
    cal = cal or InMemoryCalendar()
    agent = build_agent(model, cal, docs, LA)
    out = agent.invoke({"messages": [("user", text)]}, CFG)
    return out, model, cal


def test_create_event_via_tool_call():
    out, _, cal = run(
        [
            call("create_event", {"title": "Lunch", "start": "2026-11-06T12:00:00", "end": "2026-11-06T13:00:00"}),
            AIMessage(content="Created Lunch."),
        ]
    )
    assert [e.title for e in cal.events.values()] == ["Lunch"]
    assert out["messages"][-1].content == "Created Lunch."


def test_unknown_event_id_returns_error_to_model_not_crash():
    out, _, _ = run([call("delete_event", {"event_id": "nope"}), AIMessage(content="Couldn't find it.")])
    tool_msg = [m for m in out["messages"] if m.type == "tool"][0]
    assert tool_msg.content.startswith("Error: no event")


def test_system_prompt_has_time_and_timezone_each_call():
    _, model, _ = run([AIMessage(content="ok")])
    system = model.seen[0][0]
    assert system.type == "system"
    assert "America/Los_Angeles" in system.content and str(datetime.now(LA).year) in system.content


def test_document_search_tool_available():
    out, _, _ = run([call("search_documents", {"query": "standup"}), AIMessage(content="9:30.")], docs=CannedDocs())
    tool_msg = [m for m in out["messages"] if m.type == "tool"][0]
    assert "9:30am" in tool_msg.content and "team.md" in tool_msg.content


def test_dst_offset_is_computed_by_code_not_model():
    # 2026-11-01 is the day US DST ends: 3pm is PST (-08:00), not PDT (-07:00).
    _, _, cal = run(
        [
            call("create_event", {"title": "Dentist", "start": "2026-11-01T15:00:00", "end": "2026-11-01T16:00:00"}),
            AIMessage(content="ok"),
        ]
    )
    ev = next(iter(cal.events.values()))
    assert ev.start.utcoffset().total_seconds() == -8 * 3600
    # Before the change the same wall-clock time is PDT.
    assert LA.utcoffset(datetime(2026, 10, 31, 15)).total_seconds() == -7 * 3600


def test_model_sending_an_offset_is_rejected_with_feedback():
    out, _, cal = run(
        [
            call("create_event", {"title": "X", "start": "2026-11-01T15:00:00-07:00", "end": "2026-11-01T16:00:00-07:00"}),
            AIMessage(content="retry"),
        ]
    )
    assert not cal.events
    assert [m for m in out["messages"] if m.type == "tool"][0].status == "error"
