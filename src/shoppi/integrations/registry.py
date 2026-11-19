"""The integration registry: the one place the rest of the app asks for an
external capability. Add an integration by adding an instance to `REGISTRY`."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select

from shoppi.db.models import IntegrationCall, IntegrationCredential
from shoppi.integrations.base import (
    CalendarMirror, Integration, IntegrationError, PartsCatalogProvider, TranscriptSource, VinDecoder,
)
from shoppi.integrations.credentials import decrypt
from shoppi.integrations.google import GoogleCalendarMirror
from shoppi.integrations.parts import CsvCatalogProvider, DemoCatalogProvider
from shoppi.integrations.transcripts import SignedJsonTranscriptSource
from shoppi.integrations.vin import NhtsaVpicDecoder

REGISTRY: dict[str, Integration] = {}


def register(integration: Integration) -> None:
    REGISTRY[integration.key] = integration


for _i in (NhtsaVpicDecoder(), CsvCatalogProvider(), DemoCatalogProvider(), SignedJsonTranscriptSource(), GoogleCalendarMirror()):
    register(_i)


def get(key: str) -> Integration:
    if key not in REGISTRY:
        raise IntegrationError(f"Unknown integration: {key}")
    return REGISTRY[key]


def credential_row(ctx, key: str) -> IntegrationCredential | None:
    return ctx.tx.scalar(select(IntegrationCredential).where(IntegrationCredential.key == key))


def is_enabled(ctx, integration: Integration) -> bool:
    row = credential_row(ctx, integration.key)
    if row is not None:
        return row.status == "connected"
    return integration.connect == "none" and integration.enabled_by_default


def creds_for(ctx, key: str) -> dict:
    row = credential_row(ctx, key)
    return decrypt(row.secret) if row and row.secret else {}


# ---- Outbound calls: timing, logging, per-shop rate limit -------------------------

_RATE: dict[tuple[UUID, str], deque] = defaultdict(deque)
RATE_LIMIT_PER_MINUTE = 60


def call(ctx, key: str, operation: str, fn: Callable[[], Any]) -> Any:
    """Run one provider call. Records it for the health view and turns any failure
    into an IntegrationError with a message that is safe to show."""
    window = _RATE[(ctx.actor.shop_id, key)]
    now = time.monotonic()
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= RATE_LIMIT_PER_MINUTE:
        raise IntegrationError("Too many requests to this service in the last minute. Try again shortly.")
    window.append(now)

    started = time.perf_counter()
    ok, status = True, "ok"
    try:
        return fn()
    except IntegrationError as e:
        ok, status = False, str(e)[:200]
        raise
    except Exception as e:  # never leak provider internals or stack traces to the UI
        ok, status = False, type(e).__name__
        raise IntegrationError(f"{get(key).name} is unavailable right now.") from e
    finally:
        _log_call(ctx.actor, key, operation, ok, status, int((time.perf_counter() - started) * 1000))


def _log_call(actor, key: str, operation: str, ok: bool, status: str, latency_ms: int) -> None:
    """Record the call in its own transaction, so a failure is still logged when the
    request that made it rolls back."""
    from shoppi.db.session import tenant_tx

    try:
        with tenant_tx(actor) as tx:
            tx.add(IntegrationCall(key=key, operation=operation, ok=ok, status=status, latency_ms=latency_ms))
    except Exception:
        pass


# ---- Capabilities -----------------------------------------------------------------


def vin_decoder(ctx) -> tuple[str, VinDecoder] | None:
    for key, integration in REGISTRY.items():
        if isinstance(integration, VinDecoder) and is_enabled(ctx, integration):
            return key, integration
    return None


def parts_catalogs(ctx) -> list[tuple[str, PartsCatalogProvider, dict]]:
    return [(key, i, creds_for(ctx, key)) for key, i in REGISTRY.items()
            if isinstance(i, PartsCatalogProvider) and is_enabled(ctx, i)]


def transcript_source(key: str) -> TranscriptSource:
    integration = get(key)
    if not isinstance(integration, TranscriptSource):
        raise IntegrationError("That integration does not receive call transcripts.")
    return integration


# ---- Calendar mirroring -----------------------------------------------------------

_sync_lock = threading.Lock()
_syncing: set[UUID] = set()


def sync_calendar(shop_id: UUID) -> dict | None:
    """Push the built-in calendar to every connected external calendar. Returns counts, or None if none is connected."""
    from shoppi.core.actor import Actor
    from shoppi.core.commands import _ctx
    from shoppi.core.commands import QUERIES
    from shoppi.core.timeutil import to_local
    from shoppi.db.session import tenant_tx

    actor = Actor.system(shop_id, "integration:calendar")
    result = None
    with tenant_tx(actor, command="integrations.sync_calendar", vertical="admin") as tx:
        ctx = _ctx(tx, actor)
        for key, integration in REGISTRY.items():
            if not isinstance(integration, CalendarMirror) or not is_enabled(ctx, integration):
                continue
            row = credential_row(ctx, key)
            options = row.options or {}
            now = datetime.now(timezone.utc)
            feed = QUERIES["calendar.feed"]
            args = feed.args_model.model_validate({
                "start": to_local(now - timedelta(days=1), ctx.tz),
                "end": to_local(now + timedelta(days=int(options.get("days_ahead", 30))), ctx.tz),
                "kinds": options.get("kinds") or ["job", "event"]})
            events = [e for e in feed.handler(ctx, args) if e.get("status") != "needs_reassignment"]
            creds = decrypt(row.secret)
            result = call(ctx, key, "sync", lambda: integration.sync(ctx, creds, options, events))
    return result


def calendar_changed(shop_id: UUID) -> None:
    """Called after a booking or event change commits. Runs in the background and
    never raises: the external calendar is only a mirror."""

    def run():
        try:
            sync_calendar(shop_id)
        except Exception:
            pass
        finally:
            with _sync_lock:
                _syncing.discard(shop_id)

    with _sync_lock:
        if shop_id in _syncing:
            return
        _syncing.add(shop_id)
    threading.Thread(target=run, daemon=True, name="calendar-sync").start()
