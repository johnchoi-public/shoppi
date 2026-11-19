"""Parts catalog providers that need no vendor account.

- CsvCatalogProvider: searches a supplier price file the shop uploaded.
- DemoCatalogProvider: a small built-in sample catalog for demos and tests.

A commercial catalog (PartsTech, Nexpart, WorldPac, ...) is added the same way:
subclass Integration + PartsCatalogProvider with connect = "api_key", declare
the credential `fields`, and call the vendor's API in `search` / `place_order`
using the `creds` dict the registry passes in.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import or_, select

from shoppi.db.models import CatalogItem
from shoppi.integrations.base import (
    Availability, CatalogPart, Integration, OrderLine, PartsCatalogProvider, VehicleRef,
)


class CsvCatalogProvider(Integration, PartsCatalogProvider):
    key = "csv_catalog"
    name = "Supplier price file (CSV)"
    description = ("Upload a supplier's price list as a CSV file (columns: part_number, description, brand, fitment, "
                   "list_price, cost, qty_available, eta_days) and search it like an online catalog.")
    category = "parts"
    connect = "none"
    enabled_by_default = True

    def search(self, ctx, creds: dict, vehicle: VehicleRef | None, query: str) -> list[CatalogPart]:
        words = [w for w in query.split() if w]
        if not words:
            return []
        q = select(CatalogItem).limit(25)
        for w in words:
            like = f"%{w}%"
            q = q.where(or_(CatalogItem.part_number.ilike(like), CatalogItem.description.ilike(like),
                            CatalogItem.brand.ilike(like), CatalogItem.fitment.ilike(like)))
        out = []
        for item in ctx.tx.scalars(q):
            out.append(CatalogPart(
                provider=self.key, supplier=item.supplier_name, part_number=item.part_number, description=item.description,
                brand=item.brand, fitment_notes=item.fitment,
                list_price=float(item.list_price) if item.list_price is not None else None,
                cost=float(item.cost) if item.cost is not None else None,
                availability=[Availability("Supplier", item.qty_available, item.eta_days)],
            ))
        return out


_DEMO = [
    ("ACD-41-993", "ACDelco", "Iridium spark plug", "Most GM 5.3L / 6.2L V8", 12.99, 7.80, 40, 0),
    ("MC-FL-500S", "Motorcraft", "Engine oil filter", "Ford 2.7L / 3.5L / 5.0L", 9.49, 5.10, 60, 0),
    ("WIX-57060", "WIX", "Engine oil filter", "GM 4.3L / 5.3L / 6.2L", 8.99, 4.75, 35, 0),
    ("BOS-BC1414", "Bosch", "Ceramic front brake pads", "Ford F-150 2015-2023", 74.99, 41.20, 12, 0),
    ("AKE-ACT1084", "Akebono", "Ceramic front brake pads", "Toyota Camry 2018-2024", 68.50, 39.00, 8, 1),
    ("RAY-980726R", "Raybestos", "Front brake rotor", "Ford F-150 2015-2023", 89.00, 52.40, 6, 1),
    ("BEN-MKD1680FM", "Bendix", "Air disc brake pads", "Class 8 tractor, Bendix ADB22X", 164.00, 98.00, 4, 2),
    ("FLT-FF5825NN", "Fleetguard", "Fuel filter / water separator", "Cummins ISX15 / X15", 58.00, 33.50, 10, 1),
    ("DON-P551807", "Donaldson", "Lube filter", "Detroit DD13 / DD15", 42.00, 24.90, 14, 1),
    ("GAT-K060923", "Gates", "Serpentine belt", "GM 5.3L 2014-2019", 39.99, 21.75, 9, 0),
    ("DEN-471-1630", "Denso", "A/C compressor", "Honda Accord 2.4L 2013-2017", 389.00, 242.00, 2, 3),
    ("INT-MTP-65HD", "Interstate", "Battery group 65", "Ford trucks", 189.95, 121.00, 7, 0),
]


class DemoCatalogProvider(Integration, PartsCatalogProvider):
    key = "demo_catalog"
    name = "Demo parts catalog (sample data)"
    description = "A small built-in sample catalog for trying out parts search and ordering. Not a real supplier."
    category = "parts"
    connect = "none"
    supports_ordering = True
    orders: list[tuple[str, list[OrderLine]]] = []  # what tests and demos "ordered"

    def search(self, ctx, creds: dict, vehicle: VehicleRef | None, query: str) -> list[CatalogPart]:
        words = [w.lower() for w in query.split() if w]
        out = []
        for number, brand, desc, fit, price, cost, qty, eta in _DEMO:
            haystack = f"{number} {brand} {desc} {fit}".lower()
            if words and all(w in haystack for w in words):
                out.append(CatalogPart(self.key, "Demo Parts Supply", number, desc, brand, fit, price, cost,
                                       [Availability("Local warehouse", qty, eta)]))
        return out

    def place_order(self, ctx, creds: dict, lines: list[OrderLine]) -> str:
        ref = "DEMO-" + uuid4().hex[:8].upper()
        DemoCatalogProvider.orders.append((ref, lines))
        return ref
