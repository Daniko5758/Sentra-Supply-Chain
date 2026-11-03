from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from mock_s4 import config
from mock_s4.db import Base


class Supplier(Base):
    __tablename__ = "supplier"

    supplier_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)


class Material(Base):
    __tablename__ = "material"

    material_id: Mapped[str] = mapped_column(String, primary_key=True)
    description: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    base_unit: Mapped[str] = mapped_column(String, nullable=False)
    std_price: Mapped[int] = mapped_column(Integer, nullable=False)
    main_supplier_id: Mapped[str] = mapped_column(ForeignKey("supplier.supplier_id"), nullable=False)
    # DATA_MODEL v1.6: cost of expediting one unit (option D); null when the part cannot be expedited.
    expedite_cost_per_unit: Mapped[int | None] = mapped_column(Integer)


class Product(Base):
    __tablename__ = "product"

    product_id: Mapped[str] = mapped_column(String, primary_key=True)
    description: Mapped[str] = mapped_column(String, nullable=False)
    sales_price: Mapped[int] = mapped_column(Integer, nullable=False)


class BomItem(Base):
    __tablename__ = "bom_item"

    product_id: Mapped[str] = mapped_column(ForeignKey("product.product_id"), primary_key=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("material.material_id"), primary_key=True)
    qty_per_unit: Mapped[int] = mapped_column(Integer, nullable=False)


class Stock(Base):
    __tablename__ = "stock"

    material_id: Mapped[str] = mapped_column(ForeignKey("material.material_id"), primary_key=True)
    unrestricted_qty: Mapped[int] = mapped_column(Integer, nullable=False)


class PurchaseOrderHeader(Base):
    __tablename__ = "po_header"

    po_id: Mapped[str] = mapped_column(String, primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("supplier.supplier_id"), nullable=False)
    created_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    # DATA_MODEL v1.7 (DEC-049): organisation fields. Defaults come from `config`, not from the seed.
    purchase_order_type: Mapped[str] = mapped_column(String, nullable=False, default=config.PURCHASE_ORDER_TYPE)
    company_code: Mapped[str] = mapped_column(String, nullable=False, default=config.COMPANY_CODE)
    purch_org: Mapped[str] = mapped_column(String, nullable=False, default=config.PURCHASING_ORGANIZATION)
    purch_group: Mapped[str] = mapped_column(String, nullable=False, default=config.PURCHASING_GROUP)
    currency: Mapped[str] = mapped_column(String, nullable=False, default=config.DOCUMENT_CURRENCY)
    created_by: Mapped[str] = mapped_column(String, nullable=False, default=config.SEED_CREATED_BY)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # null for the seed
    # B-53: 02 Active, 03 In release, 05 Release completed (every PO of the seed), 08 Rejected (only made by tests).
    processing_status: Mapped[str] = mapped_column(String(2), nullable=False, default="05")
    release_not_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


def _base_unit_of_material(context) -> str:
    """Default of `po_item.unit`: the base unit of the material of the row being inserted."""
    material_id = context.get_current_parameters()["material_id"]
    return context.connection.execute(select(Material.base_unit).where(Material.material_id == material_id)).scalar_one()


class PurchaseOrderItem(Base):
    __tablename__ = "po_item"

    po_id: Mapped[str] = mapped_column(ForeignKey("po_header.po_id"), primary_key=True)
    item_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("material.material_id"), nullable=False)
    order_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    net_price: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    # DATA_MODEL v1.6: how the goods travel (FCA/FOB, ROAD/SEA/AIR); freight is only filled by CREATE_PR_PO actions.
    incoterms: Mapped[str] = mapped_column(String, nullable=False, default="FCA")
    shipping_condition: Mapped[str] = mapped_column(String, nullable=False, default="ROAD")
    freight_per_unit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # DATA_MODEL v1.7 (DEC-049). `unit` defaults to the base unit of the material (the seed loader and E-14 set it explicitly).
    plant: Mapped[str] = mapped_column(String, nullable=False, default=config.PLANT)
    unit: Mapped[str] = mapped_column(String, nullable=False, default=_base_unit_of_material)
    net_price_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    currency: Mapped[str] = mapped_column(String, nullable=False, default=config.DOCUMENT_CURRENCY)
    pr_ref: Mapped[str | None] = mapped_column(String)  # the purchase requisition this item was made from (E-14)
    pr_item_no: Mapped[int | None] = mapped_column(Integer)
    # DATA_MODEL v1.7, B-52: the deletion indicator ('' or 'L'); 'L' always comes with status CANCELLED and leaves `order_qty` alone.
    deletion_code: Mapped[str] = mapped_column(String(1), nullable=False, default="")


class PurchaseOrderScheduleLine(Base):
    __tablename__ = "po_schedule_line"

    po_id: Mapped[str] = mapped_column(String, primary_key=True)
    item_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    confirmed_date: Mapped[date] = mapped_column(Date, nullable=False)
    __table_args__ = (ForeignKeyConstraint(["po_id", "item_no"], ["po_item.po_id", "po_item.item_no"]),)


class PurchaseRequisition(Base):
    __tablename__ = "purchase_requisition"

    pr_id: Mapped[str] = mapped_column(String, primary_key=True)
    item_no: Mapped[int] = mapped_column(Integer, nullable=False)
    material_id: Mapped[str] = mapped_column(ForeignKey("material.material_id"), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    fixed_supplier_id: Mapped[str | None] = mapped_column(ForeignKey("supplier.supplier_id"))
    delivery_date: Mapped[date] = mapped_column(Date, nullable=False)
    po_ref: Mapped[str | None] = mapped_column(ForeignKey("po_header.po_id"))
    # DATA_MODEL v1.7 (DEC-049): `processing_status` replaces the old `status` (OPEN/ORDERED): N = not processed, B = PO created.
    pr_type: Mapped[str] = mapped_column(String, nullable=False)
    plant: Mapped[str] = mapped_column(String, nullable=False)
    base_unit: Mapped[str] = mapped_column(String, nullable=False)
    purch_group: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processing_status: Mapped[str] = mapped_column(String(1), nullable=False, default="N")


class ProductionOrder(Base):
    __tablename__ = "production_order"

    order_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("product.product_id"), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    sales_order_id: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False)


class SalesOrder(Base):
    __tablename__ = "sales_order"

    so_id: Mapped[str] = mapped_column(String, primary_key=True)
    customer: Mapped[str] = mapped_column(String, nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey("product.product_id"), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_delivery_date: Mapped[date] = mapped_column(Date, nullable=False)
    confirmed_delivery_date: Mapped[date] = mapped_column(Date, nullable=False)


class ChangeDocument(Base):
    __tablename__ = "change_document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    object_type: Mapped[str] = mapped_column(String, nullable=False)
    object_id: Mapped[str] = mapped_column(String, nullable=False)
    field: Mapped[str] = mapped_column(String, nullable=False)
    old_value: Mapped[str | None] = mapped_column(String)
    new_value: Mapped[str | None] = mapped_column(String)
    changed_by: Mapped[str] = mapped_column(String, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    # DATA_MODEL v1.7 (DEC-049), the shape of CDHDR/CDPOS: one `change_number` per write, one row per field.
    change_number: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # 0000100001, 0000100002, ...
    object_class: Mapped[str] = mapped_column(String, nullable=False)  # EINKBELEG, BANF, ZPRODORD, VERKBELEG
    object_key: Mapped[str] = mapped_column(String, nullable=False)  # the document number
    table_name: Mapped[str] = mapped_column(String, nullable=False)  # EKET, EKPO, EKKO, EBAN, AFKO, VBEP
    table_key: Mapped[str] = mapped_column(String, nullable=False)  # document + item (5 digits) + schedule line (4 digits)
    field_name: Mapped[str] = mapped_column(String, nullable=False)  # EINDT, MENGE, LOEKZ, ...
    field_label: Mapped[str] = mapped_column(String, nullable=False)
    change_ind: Mapped[str] = mapped_column(String(1), nullable=False)  # U, I or D
    tcode: Mapped[str] = mapped_column(String, nullable=False)  # ME22N, ME21N, ME51N, ME29N, CO02, VA02
    username: Mapped[str] = mapped_column(String(12), nullable=False)  # SAP form of X-Changed-By (E.2.1)


class IdempotencyRecord(Base):
    """B-44: the answer to a write request, kept by its `X-Idempotency-Key` (E.2, UT-12). Not an S/4 table; only the key
    discipline of SENTRA needs it. A request that failed is not recorded, so its key can be used again once the cause is fixed."""

    __tablename__ = "idempotency_record"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String, nullable=False)  # method, path and body of the first request
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sap_message: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # v1.7: the header `sap-message` of that answer, replayed with it


MOCK_S4_MODELS = (
    Supplier,
    Material,
    Product,
    BomItem,
    Stock,
    PurchaseOrderHeader,
    PurchaseOrderItem,
    PurchaseOrderScheduleLine,
    PurchaseRequisition,
    ProductionOrder,
    SalesOrder,
    ChangeDocument,
    IdempotencyRecord,
)


def register_mock_models() -> None:
    """Importing this module registers the ERP schema with its own Base."""
