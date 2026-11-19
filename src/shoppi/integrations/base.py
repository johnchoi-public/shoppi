"""Integration interfaces.

An integration is one class registered in `registry.py`. The rest of the app
asks the registry for a *capability* (decode a VIN, search parts catalogs,
mirror the calendar, receive call transcripts) and never talks to a vendor
directly, so adding or swapping a provider touches nothing else.

Two connect styles are supported, both started from Settings -> Integrations:
- "oauth":   the user clicks Connect and signs in at the provider (Google);
- "api_key": the user pastes credentials into a short form (parts catalogs);
- "none":    nothing to connect; it can just be switched on or off.
Secrets are stored encrypted (credentials.py) and never sent back to the browser.

To add an integration: subclass `Integration` plus the capability interface(s)
it provides, implement the methods, and add an instance to REGISTRY.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class IntegrationError(Exception):
    """A provider call failed. The message is safe to show to the user."""


@dataclass(frozen=True)
class CredentialField:
    name: str
    label: str
    secret: bool = True
    required: bool = True
    help: str = ""


class Integration(ABC):
    key: str  # stable identifier, e.g. "google_calendar"
    name: str
    description: str
    category: str  # calendar | vehicle | parts | telephony
    connect: str = "none"  # oauth | api_key | none
    fields: tuple[CredentialField, ...] = ()
    enabled_by_default: bool = False

    def is_available(self) -> tuple[bool, str]:
        """Whether this deployment can offer the integration (e.g. OAuth client configured)."""
        return True, ""

    def validate_credentials(self, values: dict[str, str]) -> None:
        """Raise IntegrationError if the pasted credentials are unusable."""

    # -- oauth --
    def oauth_url(self, state: str, redirect_uri: str) -> str:
        raise NotImplementedError

    def oauth_exchange(self, params: dict[str, str], redirect_uri: str) -> tuple[dict, dict]:
        """Finish the OAuth flow. Returns (secret values, non-secret options)."""
        raise NotImplementedError


# ---- VIN decoding -----------------------------------------------------------------


@dataclass
class DecodedVehicle:
    vin: str
    year: int | None = None
    make: str | None = None
    model: str | None = None
    trim: str | None = None
    body_class: str | None = None
    engine: str | None = None
    powertrain: str | None = None  # gas | diesel | hybrid | ev
    drive_type: str | None = None
    gvwr_class: int | None = None  # 1-8
    source: str = ""

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


class VinDecoder(ABC):
    @abstractmethod
    def decode(self, vin: str) -> DecodedVehicle: ...


# ---- Parts catalogs ---------------------------------------------------------------


@dataclass
class Availability:
    location: str
    qty: int | None = None
    eta_days: int | None = None


@dataclass
class CatalogPart:
    provider: str
    supplier: str
    part_number: str
    description: str
    brand: str | None = None
    fitment_notes: str | None = None
    list_price: float | None = None
    cost: float | None = None
    availability: list[Availability] = field(default_factory=list)

    def as_dict(self) -> dict:
        etas = [a.eta_days for a in self.availability if a.eta_days is not None and (a.qty is None or a.qty > 0)]
        return {
            "provider": self.provider, "supplier": self.supplier, "part_number": self.part_number,
            "description": self.description, "brand": self.brand, "fitment_notes": self.fitment_notes,
            "list_price": self.list_price, "cost": self.cost,
            "availability": [a.__dict__ for a in self.availability],
            "best_eta_days": min(etas) if etas else None,
            "in_stock_qty": sum(a.qty or 0 for a in self.availability),
        }


@dataclass
class OrderLine:
    part_number: str
    qty: int
    brand: str | None = None


@dataclass
class VehicleRef:
    year: int | None = None
    make: str | None = None
    model: str | None = None
    engine: str | None = None
    vin: str | None = None


class PartsCatalogProvider(ABC):
    supports_ordering: bool = False

    @abstractmethod
    def search(self, ctx: Any, creds: dict, vehicle: VehicleRef | None, query: str) -> list[CatalogPart]:
        """Find parts by description or part number, optionally for a specific vehicle."""

    def check_availability(self, ctx: Any, creds: dict, part_numbers: list[str]) -> list[CatalogPart]:
        out: list[CatalogPart] = []
        for number in part_numbers:
            out.extend(p for p in self.search(ctx, creds, None, number) if p.part_number.lower() == number.lower())
        return out

    def place_order(self, ctx: Any, creds: dict, lines: list[OrderLine]) -> str:
        """Place a real order with the supplier. Returns the supplier's order reference."""
        raise IntegrationError("This catalog does not support ordering.")


# ---- Phone call transcripts -------------------------------------------------------


@dataclass
class TranscriptSegment:
    speaker: str
    text: str
    start_s: float | None = None


@dataclass
class CallTranscript:
    external_id: str
    segments: list[TranscriptSegment]
    direction: str = "inbound"
    from_number: str | None = None
    to_number: str | None = None
    started_at: datetime | None = None
    duration_s: int | None = None
    language: str | None = None
    consent_noted: bool = False

    @property
    def text(self) -> str:
        return "\n".join(f"{s.speaker}: {s.text}" for s in self.segments)


class TranscriptSource(ABC):
    """A telephony or speech-to-text provider that delivers finished call transcripts.

    The provider posts to /api/integrations/<key>/webhook/<shop hook id>.
    """

    @abstractmethod
    def verify_webhook(self, headers: dict[str, str], body: bytes, creds: dict) -> bool:
        """Is this request really from the provider? (signature check)"""

    @abstractmethod
    def parse(self, payload: dict) -> CallTranscript:
        """Provider payload -> the normalised transcript."""


# ---- External calendars -----------------------------------------------------------


class CalendarMirror(ABC):
    """Pushes the shop's built-in calendar to an external one. One-way: the
    built-in calendar is always the source of truth."""

    @abstractmethod
    def sync(self, ctx: Any, creds: dict, options: dict, events: list[dict]) -> dict:
        """Make the external calendar match `events`. Returns counts for the health view."""
