"""Who is doing something, and for which shop.

An `Actor` is created once per request (from the session cookie, an intake link,
a webhook, ...) and passed down to every command and query. Nothing below the
web layer ever reads a shop id from user input.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

# How the actor reached us. Recorded on every history row.
KINDS = ("user", "agent", "intake_link", "integration", "system")


@dataclass(frozen=True)
class Actor:
    shop_id: UUID
    user_id: UUID | None = None
    role: str = "mechanic"  # owner | advisor | mechanic | public | system
    kind: str = "user"
    source: str = "ui"  # ui | agent | intake_link | integration:<key> | system
    name: str = ""
    employee_id: UUID | None = None
    ref: str | None = None  # e.g. intake link id or integration key

    @property
    def is_manager(self) -> bool:
        return self.role in ("owner", "advisor")

    def as_agent(self) -> "Actor":
        """The same person, acting through the assistant (writes become proposals)."""
        return replace(self, kind="agent", source="agent")

    @staticmethod
    def system(shop_id: UUID, source: str = "system") -> "Actor":
        return Actor(shop_id=shop_id, role="system", kind="system", source=source, name="System")
