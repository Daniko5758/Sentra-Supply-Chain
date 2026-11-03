"""EP-04 schedule data (R-19) and the ERP lists of TL-01 / TL-02."""

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentra_api.erp import ErpData
from sentra_api.impact_engine import ImpactData, schedule_status
from sentra_api.models import SupplierSite


def po_schedule(session: Session, erp: ErpData, d0: date) -> dict[str, Any]:
    """`totals` and `po_lines` of EP-04: every PO line with its slip (confirmed - requested) and status."""
    status = schedule_status(ImpactData(erp, ()), d0)
    slip = {(row.po_id, row.item_no): row for row in status.po_lines}
    supplier = {site.site_id: site.supplier_name for site in session.scalars(select(SupplierSite))}
    material = {m.material_id: m.description for m in erp.materials}
    lines = [
        {
            "po_id": line.po_id, "item_no": line.item_no, "material_id": line.material_id,
            "material_name": material.get(line.material_id, line.material_id), "supplier_id": line.supplier_id,
            "supplier_name": supplier.get(line.supplier_id, line.supplier_id), "qty": line.qty,
            "requested_date": line.requested_date.isoformat(), "confirmed_date": line.confirmed_date.isoformat(),
            "slip_days": slip[(line.po_id, line.item_no)].slip_days, "status": slip[(line.po_id, line.item_no)].status,
        }
        for line in sorted(erp.po_lines, key=lambda l: (l.po_id, l.item_no))
    ]
    slipped = sum(1 for row in lines if row["status"] == "Slipped")
    short = sum(1 for order in status.production_orders if order.coverage == "Short")
    return {
        "totals": {
            "po_lines": len(lines), "slipped": slipped, "on_time": len(lines) - slipped,
            "orders_covered": len(status.production_orders) - short, "orders_short": short,
        },
        "po_lines": lines,
    }


def production_orders(erp: ErpData, d0: date) -> list[dict[str, Any]]:
    """`production_orders` of EP-04: coverage without the event (R-04 with D(p) = 0)."""
    coverage = {row.order_id: row for row in schedule_status(ImpactData(erp, ()), d0).production_orders}
    return [
        {
            "order_id": order.order_id, "product_id": order.product_id, "qty": order.qty,
            "start_date": order.start_date.isoformat(), "sales_order_id": order.sales_order_id,
            "coverage": coverage[order.order_id].coverage, "short_parts": list(coverage[order.order_id].short_parts),
        }
        for order in sorted(erp.production_orders, key=lambda o: o.order_id)
    ]


def production_schedule(erp: ErpData, d0: date) -> dict[str, Any]:
    """TL-02."""
    return {
        "production_orders": production_orders(erp, d0),
        "bom": [{"product_id": b.product_id, "material_id": b.material_id, "qty_per_unit": b.qty_per_unit}
                for b in sorted(erp.bom_items, key=lambda b: (b.product_id, b.material_id))],
        "stock": [{"material_id": s.material_id, "unrestricted_qty": s.qty}
                  for s in sorted(erp.stocks, key=lambda s: s.material_id)],
        "sales_orders": [
            {"so_id": s.so_id, "customer": s.customer, "product_id": s.product_id, "qty": s.qty,
             "confirmed_delivery_date": s.confirmed_delivery_date.isoformat()}
            for s in sorted(erp.sales_orders, key=lambda s: (s.so_id, s.item_no))
        ],
    }
