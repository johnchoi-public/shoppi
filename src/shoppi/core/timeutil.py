"""Time and serialization helpers.

Convention (unchanged from the original calendar app): the API and the assistant
speak LOCAL wall-clock times in the shop's timezone, with no UTC offset
(e.g. 2026-11-06T15:00:00). Code attaches the zone, so the offset for that
specific date (daylight saving!) comes from zoneinfo and is never guessed by a
browser or a model. The database stores timestamptz.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import inspect


def localize(dt: datetime | None, tz: ZoneInfo) -> datetime | None:
    """Naive local time -> aware datetime in the shop's zone."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(tz)
    return dt.replace(tzinfo=tz)


def to_local(dt: datetime | None, tz: ZoneInfo) -> str | None:
    """Aware datetime -> local naive ISO string."""
    if dt is None:
        return None
    return dt.astimezone(tz).replace(tzinfo=None).isoformat(timespec="seconds")


def jsonable(value, tz: ZoneInfo):
    if isinstance(value, datetime):
        return to_local(value, tz) if value.tzinfo else value.isoformat(timespec="seconds")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: jsonable(v, tz) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v, tz) for v in value]
    return value


def row_dict(row, tz: ZoneInfo, *, exclude: tuple[str, ...] = ()) -> dict:
    """Serialize an ORM row to JSON-safe values (shop-local times)."""
    hidden = {"shop_id", "token_hash", "secret", "password_hash", "embedding", *exclude}
    return {
        attr.key: jsonable(getattr(row, attr.key), tz)
        for attr in inspect(row).mapper.column_attrs
        if attr.key not in hidden
    }


_ISO_WITH_OFFSET = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?([+-]\d\d:\d\d|Z)$")


def localize_json(value, tz: ZoneInfo):
    """Rewrite timestamps inside stored JSON (history rows, change previews) as
    shop-local wall-clock times. The database renders them in its own session
    zone, which is not necessarily the shop's."""
    if isinstance(value, dict):
        return {k: localize_json(v, tz) for k, v in value.items()}
    if isinstance(value, list):
        return [localize_json(v, tz) for v in value]
    if isinstance(value, str) and _ISO_WITH_OFFSET.match(value):
        try:
            return to_local(datetime.fromisoformat(value.replace("Z", "+00:00")), tz)
        except ValueError:
            return value
    return value


def normalize_phone(phone: str | None) -> str | None:
    """Digits only, without a leading US country code, for matching callers to customers."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits or None
