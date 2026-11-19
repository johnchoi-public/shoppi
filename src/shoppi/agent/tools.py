"""The assistant's tools, generated from the command and query registries.

There is no second, hand-written write path for the model: a tool calls
`execute()` / `run_query()` exactly like a screen does, as the signed-in user
acting through the assistant. That means the same permission checks apply, and
every write becomes a proposal the user confirms.

The acting user comes from the agent's runtime context, never from tool
arguments: no tool schema contains a shop id or a user id.
"""

from __future__ import annotations

import json

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, StructuredTool

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
MAX_RESULT_CHARS = 6000


def tool_name(name: str) -> str:
    return name.replace(".", "_")


def _dump(value) -> str:
    text = json.dumps(value, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + '... (truncated: narrow the search or date range)'
    return text


def _query_tool(spec: Spec) -> BaseTool:
    def run(runtime: ToolRuntime, **kwargs) -> str:
        actor: Actor = runtime.context
        try:
            return _dump(run_query(actor, spec.name, kwargs))
        except AppError as e:
            return f"Error: {e.message}"

    return StructuredTool.from_function(func=run, name=tool_name(spec.name), description=spec.description, args_schema=spec.args_model)


def _command_tool(spec: Spec) -> BaseTool:
    def run(runtime: ToolRuntime, **kwargs) -> str:
        actor: Actor = runtime.context
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
