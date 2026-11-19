"""VIN validation and decoding.

Validation is local (length, characters, check digit) so a typo never leaves the
server. Decoding uses NHTSA's free public vPIC service, which needs no account.
"""

from __future__ import annotations

import re

import httpx

from shoppi.config import get_settings
from shoppi.integrations.base import DecodedVehicle, Integration, IntegrationError, VinDecoder

_VALUES = {**{str(d): d for d in range(10)},
           "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
           "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
           "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9}
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def normalize_vin(vin: str) -> str:
    return re.sub(r"[\s-]", "", vin or "").upper()


def vin_problem(vin: str) -> str | None:
    """Why this is not a valid 17-character VIN, or None if it is."""
    if len(vin) != 17:
        return f"A VIN has 17 characters; this has {len(vin)}."
    bad = sorted({c for c in vin if c not in _VALUES})
    if bad:
        return f"A VIN can't contain {', '.join(bad)} (the letters I, O and Q are never used)."
    total = sum(_VALUES[c] * w for c, w in zip(vin, _WEIGHTS))
    check = "X" if total % 11 == 10 else str(total % 11)
    if vin[8] != check:
        return "The VIN's check digit doesn't match: one of the characters is probably mistyped."
    return None


def _powertrain(fuel: str, fuel2: str, electrification: str) -> str | None:
    text = f"{fuel} {fuel2} {electrification}".lower()
    if not text.strip():
        return None
    if "bev" in text or ("electric" in fuel.lower() and not fuel2):
        return "ev"
    if "hybrid" in text or "phev" in text or "hev" in text or ("electric" in text and ("gasoline" in text or "diesel" in text)):
        return "hybrid"
    if "diesel" in text:
        return "diesel"
    if "gasoline" in text or "flex" in text or "natural gas" in text or "propane" in text:
        return "gas"
    return None


def _gvwr_class(text: str) -> int | None:
    m = re.search(r"class\s*([1-8])", text or "", re.I)
    return int(m.group(1)) if m else None


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_vpic(vin: str, row: dict) -> DecodedVehicle:
    """Map one row of vPIC's DecodeVinValues result to our vehicle fields."""
    g = lambda k: (row.get(k) or "").strip()  # noqa: E731
    engine_bits = []
    if g("DisplacementL"):
        try:
            engine_bits.append(f"{float(g('DisplacementL')):.1f}L")
        except ValueError:
            pass
    if g("EngineCylinders"):
        engine_bits.append(f"{g('EngineCylinders')}-cyl")
    if g("EngineModel"):
        engine_bits.append(g("EngineModel"))
    return DecodedVehicle(
        vin=vin, year=_int(g("ModelYear")), make=g("Make").title() or None, model=g("Model") or None,
        trim=g("Trim") or g("Series") or None, body_class=g("BodyClass") or None, engine=" ".join(engine_bits) or None,
        powertrain=_powertrain(g("FuelTypePrimary"), g("FuelTypeSecondary"), g("ElectrificationLevel")),
        drive_type=g("DriveType") or None, gvwr_class=_gvwr_class(g("GVWR")), source="NHTSA vPIC",
    )


class NhtsaVpicDecoder(Integration, VinDecoder):
    key = "nhtsa_vin"
    name = "VIN decoding (NHTSA)"
    description = ("Fills in year, make, model, engine, fuel type and weight class from a VIN using the US "
                   "government's free vPIC service. The VIN is the only thing sent.")
    category = "vehicle"
    connect = "none"
    enabled_by_default = True

    def decode(self, vin: str) -> DecodedVehicle:
        s = get_settings()
        try:
            resp = httpx.get(s.vin_decoder_url.format(vin=vin), timeout=s.http_timeout_s)
            resp.raise_for_status()
            results = resp.json().get("Results") or []
        except (httpx.HTTPError, ValueError) as e:
            raise IntegrationError("The VIN lookup service didn't respond. Enter the vehicle details by hand.") from e
        if not results:
            raise IntegrationError("The VIN lookup returned nothing for that VIN.")
        decoded = parse_vpic(vin, results[0])
        if not decoded.make and not decoded.model:
            raise IntegrationError("That VIN isn't recognised. Enter the vehicle details by hand.")
        return decoded
