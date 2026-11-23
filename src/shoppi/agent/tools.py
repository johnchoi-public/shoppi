"""The assistant's tools, generated from the command and query registries.

There is no second, hand-written write path for the model: a tool calls
`execute()` / `run_query()` exactly like a screen does, as the signed-in user
acting through the assistant. That means the same permission checks apply, and
every write becomes a proposal the user confirms.

The acting user comes from the agent's runtime context, never from tool
arguments: no tool schema contains a shop id or a user id.

The context is read with `get_runtime()` rather than through an injected
`runtime: ToolRuntime` parameter. LangChain calls a tool whose args schema has no
fields with no arguments at all, dropping injected ones too, so a `runtime`
parameter is missing for every no-argument tool (employees_list, bays_list, ...).
"""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, StructuredTool
from langgraph.runtime import get_runtime

from shoppi.core.actor import Actor
from shoppi.core.commands import COMMANDS, QUERIES, AppError, Spec, execute, run_query

# Curated: enough to be useful, few enough for a small local model to choose between.
AGENT_QUERIES = [
    "calendar.feed", "schedule.find_slots", "customers.list", "customers.get", "vehicles.get", "work_orders.list",
    "work_orders.get", "employees.list", "shifts.list", "bays.list", "bays.board", "services.list", "parts.list",
    "inventory.reorder_suggestions", "purchase_orders.list", "intake.list", "history.list", "documents.search",
    "vin.decode", "catalog.search", "calls.list",
]
AGENT_COMMANDS = [
    "events.create", "events.update", "events.delete", "schedule.book", "schedule.move", "schedule.unschedule",
    "customers.create", "customers.update", "vehicles.create", "vehicles.update", "work_orders.create",
    "work_orders.add_line", "work_orders.set_status", "shifts.create", "time_off.create", "bays.close",
    "parts.adjust", "purchase_orders.create", "intake.staff_create",
]
# When one of these queries returns nothing, a second query says why. A bare [] makes a
# model retry with other words, or tell the user "there is no such document" when the
# truth is that nothing could be indexed.
EXPLAIN_EMPTY = {"documents.search": "documents.search_status"}
MAX_RESULT_CHARS = 6000


def tool_name(name: str) -> str:
    return name.replace(".", "_")


def _dump(value) -> str:
    text = json.dumps(value, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + '... (truncated: narrow the search or date range)'
    return text


def _actor() -> Actor:
    """The signed-in user this tool call is acting for (the agent's runtime context)."""
    return get_runtime(Actor).context


def _query_tool(spec: Spec) -> BaseTool:
    def run(**kwargs) -> str:
        actor = _actor()
        try:
            result = run_query(actor, spec.name, kwargs)
            if not result and spec.name in EXPLAIN_EMPTY:
                why = run_query(actor, EXPLAIN_EMPTY[spec.name])["message"]
                return _dump({"results": [], "why": why,
                              "next": "Do not search again: other words will give the same result. Tell the user what 'why' says, including the fix if it gives one."})
            return _dump(result)
        except AppError as e:
            return f"Error: {e.message}"

    return StructuredTool.from_function(func=run, name=tool_name(spec.name), description=spec.description, args_schema=spec.args_model)


def _command_tool(spec: Spec) -> BaseTool:
    def run(**kwargs) -> str:
        actor = _actor()
        try:
            result = execute(actor.as_agent(), spec.name, kwargs)
        except AppError as e:
            return f"Error: {e.message}"
        if result["status"] == "pending":
            cr = result["change_request"]
            who = "the user" if cr["needs"] == "self" else "an owner or advisor"
            return _dump({"status": "PROPOSED_NOT_APPLIED", "change_request_id": cr["id"],
                          "message": f"Proposed. Nothing has changed yet: {who} must confirm it in the chat panel."})
        return _dump(result)

    description = spec.description + " (Creates a proposal that must be confirmed; it does not apply the change itself.)"
    return StructuredTool.from_function(func=run, name=tool_name(spec.name), description=description, args_schema=spec.args_model)


def build_tools() -> list[BaseTool]:
    import shoppi.domain  # noqa: F401  (registers commands and queries)

    tools = [_query_tool(QUERIES[n]) for n in AGENT_QUERIES if n in QUERIES and QUERIES[n].agent]
    tools += [_command_tool(COMMANDS[n]) for n in AGENT_COMMANDS if n in COMMANDS and COMMANDS[n].agent]
    return tools
