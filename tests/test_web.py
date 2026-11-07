"""Web API tests: REST endpoints and the streaming chat endpoint (fake model)."""

import json
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from shoppi.agent.build import build_agent
from shoppi.calendar.local import LocalCalendarProvider, create_db_engine
from shoppi.web.app import create_app
from tests.fakes import ScriptedChatModel

LA = ZoneInfo("America/Los_Angeles")


def make_client(tmp_path, script=None):
    cal = LocalCalendarProvider(create_db_engine(f"sqlite:///{tmp_path}/cal.db"), LA)
    agent = build_agent(ScriptedChatModel(script=script or [], seen=[]), cal, None, LA)
    return TestClient(create_app(cal, agent, LA)), cal


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path)[0]


def test_index_and_config(client):
    assert "shoppi calendar" in client.get("/").text
    assert client.get("/api/config").json() == {"timezone": "America/Los_Angeles"}


def test_event_crud_in_local_time(client):
    created = client.post(
        "/api/events", json={"title": "Lunch", "start": "2026-11-06T12:00:00", "end": "2026-11-06T13:00:00"}
    )
    assert created.status_code == 201
    ev = created.json()
    assert ev["start"] == "2026-11-06T12:00:00"  # local naive, no offset

    listed = client.get("/api/events", params={"start": "2026-11-06T00:00:00", "end": "2026-11-07T00:00:00"}).json()
    assert [e["id"] for e in listed] == [ev["id"]]

    patched = client.patch(f"/api/events/{ev['id']}", json={"title": "Team lunch"}).json()
    assert patched["title"] == "Team lunch" and patched["start"] == "2026-11-06T12:00:00"

    assert client.delete(f"/api/events/{ev['id']}").status_code == 204
    assert client.delete(f"/api/events/{ev['id']}").status_code == 404


def test_validation_errors(client):
    bad_range = {"title": "x", "start": "2026-11-06T13:00:00", "end": "2026-11-06T12:00:00"}
    assert client.post("/api/events", json=bad_range).status_code == 422
    with_offset = {"title": "x", "start": "2026-11-06T12:00:00-08:00", "end": "2026-11-06T13:00:00-08:00"}
    assert client.post("/api/events", json=with_offset).status_code == 422
    assert client.patch("/api/events/nope", json={"title": "y"}).status_code == 404


def test_chat_streams_tool_calls_and_reply(tmp_path):
    script = [
        AIMessage(
            content="",
            tool_calls=[{
                "name": "create_event",
                "args": {"title": "Dentist", "start": "2026-11-09T15:00:00", "end": "2026-11-09T16:00:00"},
                "id": "c1",
            }],
        ),
        AIMessage(content="Booked your dentist appointment."),
    ]
    client, cal = make_client(tmp_path, script)
    with client.stream("POST", "/api/chat", json={"message": "book dentist", "thread_id": "t1"}) as res:
        assert res.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in res.iter_lines() if line.startswith("data: ")]

    types = [e["type"] for e in events]
    assert types[0] == "tool_call" and events[0]["name"] == "create_event"
    assert "tool_result" in types and types[-1] == "done"
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Booked your dentist appointment."
    # The agent's change is visible through the same calendar the REST API serves.
    listed = client.get("/api/events", params={"start": "2026-11-09T00:00:00", "end": "2026-11-10T00:00:00"}).json()
    assert [e["title"] for e in listed] == ["Dentist"]


def test_chat_reports_errors_in_stream(tmp_path):
    client, _ = make_client(tmp_path, script=[])  # empty script -> model raises
    with client.stream("POST", "/api/chat", json={"message": "hi", "thread_id": "t"}) as res:
        events = [json.loads(line[6:]) for line in res.iter_lines() if line.startswith("data: ")]
    assert events[0]["type"] == "error" and events[-1]["type"] == "done"
