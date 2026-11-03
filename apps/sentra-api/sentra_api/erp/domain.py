"""ERP domain objects (snake_case). Immutable: scenarios change copies with dataclasses.replace."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class PoLine:
    po_id: str
    item_no: int
    material_id: str
    supplier_id: str
    qty: int
    net_price: int
    requested_date: date
    confirmed_date: date
    status: str = "OPEN"
    # E-02 v1.11: shipping terms of the line (EXPEDITE_SHIPMENT and CREATE_PR_PO change them).
    incoterms: str = "FCA"
    shipping_condition: str = "ROAD"
    freight_per_unit: int = 0


@dataclass(frozen=True)
class Material:
    material_id: str
    description: str
    category: str
    std_price: int
    main_supplier_id: str
    expedite_cost_per_unit: int | None = None  # E-09 v1.11, option D


@dataclass(frozen=True)
class Product:
    product_id: str
    description: str
    sales_price: int


@dataclass(frozen=True)
class BomItem:
    product_id: str
    material_id: str
    qty_per_unit: int


@dataclass(frozen=True)
class Stock:
    material_id: str
    qty: int


@dataclass(frozen=True)
class ProductionOrder:
    order_id: str
    product_id: str
    qty: int
    start_date: date
    sales_order_id: str | None
    status: str


@dataclass(frozen=True)
class SalesOrder:
    so_id: str
    item_no: int
    customer: str
    product_id: str
    qty: int
    requested_delivery_date: date
    confirmed_delivery_date: date


@dataclass(frozen=True)
class ErpData:
    """One consistent read of the simulated ERP, the input for the pure engines."""

    po_lines: tuple[PoLine, ...]
    materials: tuple[Material, ...]
    products: tuple[Product, ...]
    bom_items: tuple[BomItem, ...]
    stocks: tuple[Stock, ...]
    production_orders: tuple[ProductionOrder, ...]
    sales_orders: tuple[SalesOrder, ...]
