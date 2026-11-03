"""E-01..E-09: database-backed, read-only projections of simulated ERP data."""

import re
from typing import Annotated, TypeVar

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import String, cast, literal, select, union_all
from sqlalchemy.sql import Select

from mock_s4.config import PLANT
from mock_s4.models import (
    BomItem, Material, Product, ProductionOrder, PurchaseOrderHeader,
    PurchaseOrderItem, PurchaseOrderScheduleLine, SalesOrder, Stock, Supplier,
)
from mock_s4.schemas import (
    BomItemRecord, Collection, ProductRecord, ProductionOrderRecord,
    PurchaseOrderItemRecord, PurchaseOrderRecord, Results, SalesOrderItemRecord,
    ScheduleLineRecord, StockRecord, SupplierRecord,
)


router = APIRouter(prefix="/sap/opu/odata/sap", tags=["ERP (SIMULATED)"])
ODataFilter = Annotated[str | None, Query(alias="$filter")]
Record = TypeVar("Record", bound=BaseModel)
FILTER = re.compile(r"\s*([A-Za-z][A-Za-z0-9_]*)\s+eq\s+'((?:[^']|'')*)'\s*")


def collection(request: Request, statement: Select, schema: type[Record],
               expression: str | None) -> Collection[Record]:
    """Apply the single equality filter to public values, never to raw SQL."""
    field = value = None
    if expression is not None:
        match = FILTER.fullmatch(expression)
        if match is None or match[1] not in schema.model_fields:
            raise HTTPException(status_code=400)
        field, value = match[1], match[2].replace("''", "'")
    with request.app.state.database.session_factory() as session:
        rows = [schema.model_validate(row) for row in session.execute(statement).mappings()]
    if field is not None:
        rows = [row for row in rows if row.model_dump(mode="json")[field] is not None
                and str(row.model_dump(mode="json")[field]) == value]
    return Collection(d=Results(results=rows))


@router.get("/API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrder",
            response_model=Collection[PurchaseOrderRecord])
def purchase_orders(request: Request, filter_by: ODataFilter = None):
    h = PurchaseOrderHeader
    statement = select(
        h.po_id.label("PurchaseOrder"), h.supplier_id.label("Supplier"),
        h.created_on.label("CreationDate"), h.status.label("Status"),
        h.purchase_order_type.label("PurchaseOrderType"), h.company_code.label("CompanyCode"),
        h.purch_org.label("PurchasingOrganization"), h.purch_group.label("PurchasingGroup"),
        h.currency.label("DocumentCurrency"), h.created_on.label("PurchaseOrderDate"), h.created_by.label("CreatedByUser"),
        h.last_changed_at.label("LastChangeDateTime"), h.processing_status.label("PurchasingProcessingStatus"),
        h.release_not_completed.label("ReleaseIsNotCompleted"),
    ).order_by(h.po_id)
    return collection(request, statement, PurchaseOrderRecord, filter_by)


@router.get("/API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrderItem",
            response_model=Collection[PurchaseOrderItemRecord])
def purchase_order_items(request: Request, filter_by: ODataFilter = None):
    i, h = PurchaseOrderItem, PurchaseOrderHeader
    statement = select(
        i.po_id.label("PurchaseOrder"), cast(i.item_no, String).label("PurchaseOrderItem"),
        i.material_id.label("Material"), i.order_qty.label("OrderQuantity"),
        i.net_price.label("NetPriceAmount"), h.supplier_id.label("Supplier"),
        i.requested_date.label("RequestedDeliveryDate"), i.status.label("Status"),
        i.incoterms.label("IncotermsClassification"), i.shipping_condition.label("ShippingCondition"),
        i.freight_per_unit.label("FreightPerUnit"), i.plant.label("Plant"), i.unit.label("PurchaseOrderQuantityUnit"),
        i.net_price_qty.label("NetPriceQuantity"), i.currency.label("DocumentCurrency"),
        i.pr_ref.label("PurchaseRequisition"), cast(i.pr_item_no, String).label("PurchaseRequisitionItem"),
        i.deletion_code.label("PurchasingDocumentDeletionCode"),
    ).join(h, i.po_id == h.po_id).order_by(i.po_id, i.item_no)
    return collection(request, statement, PurchaseOrderItemRecord, filter_by)


@router.get("/API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrderScheduleLine",
            response_model=Collection[ScheduleLineRecord])
def schedule_lines(request: Request, filter_by: ODataFilter = None):
    s = PurchaseOrderScheduleLine
    statement = select(
        s.po_id.label("PurchasingDocument"), cast(s.item_no, String).label("PurchasingDocumentItem"),
        cast(s.line_no, String).label("ScheduleLine"),
        s.confirmed_date.label("ScheduleLineDeliveryDate"),
    ).order_by(s.po_id, s.item_no, s.line_no)
    return collection(request, statement, ScheduleLineRecord, filter_by)


@router.get("/API_PRODUCTION_ORDER_2_SRV/A_ProductionOrder_2",
            response_model=Collection[ProductionOrderRecord])
def production_orders(request: Request, filter_by: ODataFilter = None):
    p = ProductionOrder
    statement = select(
        p.order_id.label("ManufacturingOrder"), p.product_id.label("Product"),
        p.qty.label("MfgOrderPlannedTotalQty"), p.start_date.label("MfgOrderPlannedStartDate"),
        p.sales_order_id.label("SalesOrder"), p.status.label("Status"),
    ).order_by(p.order_id)
    return collection(request, statement, ProductionOrderRecord, filter_by)


@router.get("/API_SALES_ORDER_SRV/A_SalesOrderItem",
            response_model=Collection[SalesOrderItemRecord])
def sales_order_items(request: Request, filter_by: ODataFilter = None):
    s = SalesOrder
    statement = select(
        s.so_id.label("SalesOrder"), literal("10").label("SalesOrderItem"),
        s.customer.label("CustomerName"), s.product_id.label("Material"),
        s.qty.label("RequestedQuantity"), s.requested_delivery_date.label("RequestedDeliveryDate"),
        s.confirmed_delivery_date.label("ConfirmedDeliveryDate"),
    ).order_by(s.so_id)
    return collection(request, statement, SalesOrderItemRecord, filter_by)


@router.get("/API_MATERIAL_STOCK_SRV/A_MatlStkInAcctMod", response_model=Collection[StockRecord])
def stocks(request: Request, filter_by: ODataFilter = None):
    statement = select(
        Stock.material_id.label("Material"), literal(PLANT).label("Plant"),
        Stock.unrestricted_qty.label("MatlWrhsStkQtyInMatlBaseUnit"),
    ).order_by(Stock.material_id)
    return collection(request, statement, StockRecord, filter_by)


@router.get("/API_BILL_OF_MATERIAL_SRV/MaterialBOMItem", response_model=Collection[BomItemRecord])
def bom_items(request: Request, filter_by: ODataFilter = None):
    b = BomItem
    statement = select(
        b.product_id.label("Material"), b.material_id.label("BillOfMaterialComponent"),
        b.qty_per_unit.label("BillOfMaterialItemQuantity"),
    ).order_by(b.product_id, b.material_id)
    return collection(request, statement, BomItemRecord, filter_by)


@router.get("/API_BUSINESS_PARTNER/A_Supplier", response_model=Collection[SupplierRecord])
def suppliers(request: Request, filter_by: ODataFilter = None):
    statement = select(
        Supplier.supplier_id.label("Supplier"), Supplier.name.label("SupplierName"),
        Supplier.country.label("Country"),
    ).order_by(Supplier.supplier_id)
    return collection(request, statement, SupplierRecord, filter_by)


@router.get("/API_PRODUCT_SRV/A_Product", response_model=Collection[ProductRecord])
def products(request: Request, filter_by: ODataFilter = None):
    m, p = Material, Product
    # A.2: fields absent from the relevant master record are null.
    parts = select(
        m.material_id.label("Product"), m.description.label("ProductDescription"),
        literal("ROH").label("ProductType"), m.category.label("ProductCategory"),
        m.std_price.label("StandardPrice"), literal(None).label("SalesPrice"),
        m.main_supplier_id.label("MainSupplier"), m.expedite_cost_per_unit.label("ExpediteCostPerUnit"),
    )
    finished = select(
        p.product_id.label("Product"), p.description.label("ProductDescription"),
        literal("FERT").label("ProductType"), literal(None).label("ProductCategory"),
        literal(None).label("StandardPrice"), p.sales_price.label("SalesPrice"),
        literal(None).label("MainSupplier"), literal(None).label("ExpediteCostPerUnit"),
    )
    catalog = union_all(parts, finished).subquery()
    return collection(request, select(catalog).order_by(catalog.c.Product), ProductRecord, filter_by)
