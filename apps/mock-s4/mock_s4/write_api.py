"""E-11..E-18 (API_CONTRACT E.2): the writes of F-12, the change log and the release of a PO (E-18, B-53). They exist only with FEATURE_ERP_EXECUTION.

Every write needs `X-Idempotency-Key` (the same key gives the same answer and no second document, UT-12). Since v1.12 (DEC-049) a write
answers like SAP: the header `sap-message` on success, the OData V2 error on a refusal (`messages.py`, E.2.2), and it records one
`change_number` with one `change_document` row per changed field, in the shape of CDHDR/CDPOS (E.2.3; `source = SENTRA`, `changed_by`
from `X-Changed-By`, `username` its SAP form). The contract fixes the paths and the fields that change; the bodies of E-13/E-14 are the ones of E.2 (v1.12, B-51):

- E-13 deep insert `{"PurchaseRequisitionType", "to_PurchaseReqnItem": {"results": [{"Material", "Plant", "RequestedQuantity",
  "BaseUnit"?, "DeliveryDate", "FixedSupplier", "PurchasingGroup", "PurchaseRequisitionPrice"?}]}}` (exactly one item)
  -> `{"d": {"PurchaseRequisition": "0010000001", "PurchaseRequisitionType", "to_PurchaseReqnItem": {...}}}`
- E-14 `{"PurchaseOrderType", "CompanyCode", "PurchasingOrganization", "PurchasingGroup", "DocumentCurrency"?, "Supplier",
  "to_PurchaseOrderItem": {"results": [{"Material", "Plant", "OrderQuantity", "PurchaseOrderQuantityUnit"?, "NetPriceAmount",
  "NetPriceQuantity"?, "RequestedDeliveryDate", "PurchaseRequisition"? + "PurchaseRequisitionItem"?, "IncotermsClassification"?,
  "ShippingCondition"?, "FreightPerUnit"?, "PurchaseOrderItem"?}]}}`; schedule line 1 of every item takes the requested delivery date.

The organisation fields are required but only checked for "not empty" (ZMOCK_S4/007): this mock has no customizing to check them
against (the constants of `config.py` are only the defaults of the seed and of `DocumentCurrency`).

There is no SENTRA logic here (no rule about which change is sensible): the ERP stores what it is told and refuses only what it
cannot represent.
"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from mock_s4 import config
from mock_s4 import messages as sap
from mock_s4.errors import MockApiError
from mock_s4.messages import SapError, SapMessage, SapWriteRoute
from mock_s4.models import (
    ChangeDocument, IdempotencyRecord, Material, ProductionOrder, PurchaseOrderHeader, PurchaseOrderItem,
    PurchaseOrderScheduleLine, PurchaseRequisition, SalesOrder, Supplier,
)
from mock_s4.schemas import (
    ChangeDocumentRecord, Collection, ProductionOrderRecord, PurchaseOrderItemRecord, PurchaseOrderRecord,
    PurchaseRequisitionItemRecord, PurchaseRequisitionRecord, Results, SalesOrderItemRecord, ScheduleLineRecord,
)
from mock_s4.seed_data import jakarta_today


def require_write_enabled(request: Request) -> None:
    if not request.app.state.settings.feature_erp_execution:
        raise MockApiError(404, "FEATURE_DISABLED", "This feature is disabled.")


router = APIRouter(tags=["ERP write (SIMULATED)"], dependencies=[Depends(require_write_enabled)], route_class=SapWriteRoute)
PO_SERVICE = "/sap/opu/odata/sap/API_PURCHASEORDER_PROCESS_SRV"
Key = Annotated[str, Header(alias="X-Idempotency-Key", min_length=1, max_length=200)]
ChangedBy = Annotated[str | None, Header(alias="X-Changed-By")]
SOURCE = "SENTRA"
FIRST_REQUISITION, FIRST_PURCHASE_ORDER = 10_000_001, 4_500_000_001
FIRST_CHANGE_NUMBER = 100_001
ACTIVE, IN_RELEASE, RELEASED, REJECTED = "02", "03", "05", "08"  # `PurchasingProcessingStatus` (B-53)
Work = Callable[[Session], tuple[int, dict[str, Any], str]]  # status, body, header `sap-message`


def _iso_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError("must be a date YYYY-MM-DD") from None
    return value


IsoDate = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$"), AfterValidator(_iso_date)]
Shipping = Literal["ROAD", "SEA", "AIR"]
Incoterms = Literal["FCA", "FOB"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScheduleLinePatch(_Body):
    ScheduleLineDeliveryDate: IsoDate


class ItemPatch(_Body):
    """E-12: exactly one of a quantity, the shipping pair of an expedite, or the deletion indicator `L` (400 otherwise).

    The quantity 0 passes the schema on purpose: `work` refuses it with ZMOCK_S4/008 (B-52)."""

    OrderQuantity: StrictInt | None = Field(None, ge=0)
    ShippingCondition: Shipping | None = None
    IncotermsClassification: Incoterms | None = None
    PurchasingDocumentDeletionCode: Literal["L"] | None = None

    @model_validator(mode="after")
    def _one_kind_of_change(self) -> "ItemPatch":
        shipping = (self.ShippingCondition, self.IncotermsClassification)
        kinds = [self.OrderQuantity is not None, any(value is not None for value in shipping),
                 self.PurchasingDocumentDeletionCode is not None]
        if sum(kinds) != 1:
            raise ValueError("exactly one of OrderQuantity, the shipping pair or PurchasingDocumentDeletionCode")
        if None in shipping and kinds[1]:
            raise ValueError("ShippingCondition and IncotermsClassification go together")
        return self


class ProductionOrderPatch(_Body):
    MfgOrderPlannedStartDate: IsoDate


class SalesOrderItemPatch(_Body):
    ConfirmedDeliveryDate: IsoDate


class RequisitionItemCreate(_Body):
    Material: str
    Plant: str | None = None  # required (007), checked in `work` so that "missing" and "empty" give the same answer
    RequestedQuantity: StrictInt = Field(ge=1)
    BaseUnit: str | None = None
    DeliveryDate: IsoDate
    FixedSupplier: str
    PurchasingGroup: str | None = None  # required (007)
    PurchaseRequisitionPrice: StrictInt = Field(0, ge=0)


class RequisitionItems(_Body):
    results: list[RequisitionItemCreate] = Field(min_length=1, max_length=1)  # exactly one item (E-13)


class RequisitionCreate(_Body):
    PurchaseRequisitionType: str | None = None  # required (007)
    to_PurchaseReqnItem: RequisitionItems


ItemNumber = Annotated[str, StringConstraints(pattern=r"^\d{1,5}$")]


class PurchaseOrderItemCreate(_Body):
    PurchaseOrderItem: ItemNumber | None = None
    Material: str
    Plant: str | None = None  # required (007)
    OrderQuantity: StrictInt = Field(ge=1)
    PurchaseOrderQuantityUnit: str | None = None  # default: the base unit of the material
    NetPriceAmount: StrictInt = Field(ge=0)
    NetPriceQuantity: StrictInt | None = Field(None, ge=1)  # default 1
    RequestedDeliveryDate: IsoDate
    PurchaseRequisition: Annotated[str, StringConstraints(min_length=1)] | None = None
    PurchaseRequisitionItem: ItemNumber | None = None
    IncotermsClassification: Incoterms = "FCA"
    ShippingCondition: Shipping = "ROAD"
    FreightPerUnit: StrictInt = Field(0, ge=0)

    @model_validator(mode="after")
    def _requisition_and_item_go_together(self) -> "PurchaseOrderItemCreate":
        if (self.PurchaseRequisition is None) != (self.PurchaseRequisitionItem is None):
            raise ValueError("PurchaseRequisition and PurchaseRequisitionItem go together")
        return self


class PurchaseOrderItems(_Body):
    results: list[PurchaseOrderItemCreate] = Field(min_length=1)


class ReleaseBody(_Body):
    """E-18: the body is `{}`."""


class PurchaseOrderCreate(_Body):
    PurchaseOrderType: str | None = None  # the four organisation fields are required (007)
    CompanyCode: str | None = None
    PurchasingOrganization: str | None = None
    PurchasingGroup: str | None = None
    DocumentCurrency: str | None = None  # default IDR
    Supplier: str
    to_PurchaseOrderItem: PurchaseOrderItems


# --- the change documents (E.2.3) ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldSpec:
    """One row of the mapping table of E.2.3: what SAP calls the changed field."""

    object_class: str
    table_name: str
    field_name: str
    field_label: str
    change_ind: str  # U, I or D
    tcode: str

    def __post_init__(self) -> None:
        assert self.change_ind in ("U", "I", "D")


EINDT = FieldSpec("EINKBELEG", "EKET", "EINDT", "Delivery date", "U", "ME22N")
MENGE = FieldSpec("EINKBELEG", "EKPO", "MENGE", "PO quantity", "U", "ME22N")
LOEKZ = FieldSpec("EINKBELEG", "EKPO", "LOEKZ", "Deletion indicator", "U", "ME22N")
ZZSHIPCOND = FieldSpec("EINKBELEG", "EKPO", "ZZSHIPCOND", "Shipping condition", "U", "ME22N")
INCO1 = FieldSpec("EINKBELEG", "EKPO", "INCO1", "Incoterms", "U", "ME22N")
EBAN_KEY = FieldSpec("BANF", "EBAN", "KEY", "Purchase requisition created", "I", "ME51N")
EKKO_KEY = FieldSpec("EINKBELEG", "EKKO", "KEY", "Purchase order created", "I", "ME21N")
EBAN_STATU = FieldSpec("BANF", "EBAN", "STATU", "Processing status", "U", "ME21N")
PROCSTAT = FieldSpec("EINKBELEG", "EKKO", "PROCSTAT", "Processing status", "U", "ME29N")
GSTRP = FieldSpec("ZPRODORD", "AFKO", "GSTRP", "Basic start date", "U", "CO02")
EDATU = FieldSpec("VERKBELEG", "VBEP", "EDATU", "Schedule line date", "U", "VA02")


def sap_username(changed_by: str | None) -> str:
    """E.2.1: upper case, only `A-Z0-9`, at most 12 characters; nothing left -> `SENTRA`."""
    return re.sub(r"[^A-Z0-9]", "", (changed_by or "").upper())[:12] or SOURCE


def _item_key(po_id: str, item_no: int) -> str:
    return f"{po_id}{item_no:05d}"


def _line_key(po_id: str, item_no: int, line_no: int) -> str:
    return f"{po_id}{item_no:05d}{line_no:04d}"


class Journal:
    """The change documents of one write: one `change_number` (the highest so far + 1) and one row per field."""

    def __init__(self, session: Session, changed_by: str | None) -> None:
        current = session.scalar(select(func.max(ChangeDocument.change_number)))
        self.number = f"{FIRST_CHANGE_NUMBER if current is None else int(current) + 1:010d}"
        self.session, self.changed_by, self.username = session, changed_by or SOURCE, sap_username(changed_by)

    def add(self, spec: FieldSpec, *, object_key: str, table_key: str, legacy: tuple[str, str, str], old: Any, new: Any) -> None:
        """`legacy` = the v1.11 columns `object_type`, `object_id`, `field`, which stay filled."""
        object_type, object_id, field = legacy
        self.session.add(ChangeDocument(
            object_type=object_type, object_id=object_id, field=field, old_value=None if old is None else str(old),
            new_value=None if new is None else str(new), changed_by=self.changed_by, changed_at=datetime.now(UTC), source=SOURCE,
            change_number=self.number, object_class=spec.object_class, object_key=object_key, table_name=spec.table_name,
            table_key=table_key, field_name=spec.field_name, field_label=spec.field_label, change_ind=spec.change_ind,
            tcode=spec.tcode, username=self.username,
        ))


# --- the handling of a request -----------------------------------------------------------------------------------------------

def _pairs(key: str, obj: str, *names: str) -> tuple[str, ...]:
    """`PurchaseOrder='4500001001',PurchaseOrderItem='10'` -> ('4500001001', '10'); anything else is no such object (404)."""
    pattern = r"\s*,\s*".join(f"{name}='([^']*)'" for name in names)
    found = re.fullmatch(pattern, key.strip())
    if found is None:
        raise SapError(404, sap.does_not_exist(obj, key.strip()[:60]))
    return found.groups()


def _number(text: str, obj: str, key: str) -> int:
    if not text.isdigit():
        raise SapError(404, sap.does_not_exist(obj, key))
    return int(text)


def _perform(request: Request, key: str, body: BaseModel, work: Work) -> JSONResponse:
    """Run `work` once per key in one transaction and keep its answer and its `sap-message`; a repeated key replays both."""
    canonical = json.dumps(body.model_dump(mode="json"), sort_keys=True)
    fingerprint = f"{request.method} {request.url.path} {hashlib.sha256(canonical.encode()).hexdigest()}"
    with request.app.state.database.session_factory() as session, session.begin():
        stored = session.get(IdempotencyRecord, key)
        if stored is not None:
            if stored.fingerprint != fingerprint:
                raise SapError(409, sap.idempotency_key_used())
            header = {} if stored.sap_message is None else {"sap-message": sap.dump_header(stored.sap_message)}
            return JSONResponse(stored.response, status_code=stored.status_code, headers=header)
        status_code, content, header = work(session)
        session.add(IdempotencyRecord(key=key, fingerprint=fingerprint, status_code=status_code, response=content,
                                      sap_message=json.loads(header)))
    return JSONResponse(content, status_code=status_code, headers={"sap-message": header})


def _d0(request: Request) -> date:
    return getattr(request.app.state, "d0_date", None) or jakarta_today()


def _next_number(session: Session, column, first: int) -> int:
    current = session.scalar(select(func.max(cast(column, Integer))))
    return first if not current else int(current) + 1


def _schedule_line(session: Session, po_id: str, item_no: int, line_no: int) -> PurchaseOrderScheduleLine:
    line = session.get(PurchaseOrderScheduleLine, (po_id, item_no, line_no))
    if line is None:
        raise SapError(404, sap.does_not_exist("Schedule line", f"{po_id}/{sap.item5(item_no)}/{line_no:04d}"))
    return line


def _not_rejected(header: PurchaseOrderHeader) -> None:
    """A PO in status `08` (rejected) takes no write at all, neither on the header nor on its items (ZMOCK_S4/006)."""
    if header.processing_status == REJECTED:
        raise SapError(409, sap.purchase_order_rejected(header.po_id))


def _open_item(session: Session, po_id: str, item_no: int) -> PurchaseOrderItem:
    item = session.get(PurchaseOrderItem, (po_id, item_no))
    if item is None:
        raise SapError(404, sap.does_not_exist("Purchase order item", f"{po_id}/{sap.item5(item_no)}"))
    _not_rejected(session.get(PurchaseOrderHeader, po_id))
    if item.deletion_code == "L" or item.status != "OPEN":
        raise SapError(409, sap.item_flagged_for_deletion(po_id, item_no))
    return item


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _required(*fields: tuple[str, str | None]) -> None:
    """ZMOCK_S4/007: one detail per empty (missing, null or blank) field, in the order given; the main message is the first."""
    empty = [sap.field_required(name) for name, value in fields if _blank(value)]
    if empty:
        main, *details = empty
        raise SapError(400, main, details)


def _net_value(items: list[PurchaseOrderItemCreate]) -> int:
    """The net value of a new PO (IDR): Σ quantity × price / price unit over the items; freight is not part of it."""
    return int(sum(Fraction(i.OrderQuantity * i.NetPriceAmount, i.NetPriceQuantity or 1) for i in items))


def _item_record(session: Session, item: PurchaseOrderItem) -> dict[str, Any]:
    header = session.get(PurchaseOrderHeader, item.po_id)
    return PurchaseOrderItemRecord(
        PurchaseOrder=item.po_id, PurchaseOrderItem=str(item.item_no), Material=item.material_id, OrderQuantity=item.order_qty,
        NetPriceAmount=item.net_price, Supplier=header.supplier_id, RequestedDeliveryDate=item.requested_date, Status=item.status,
        IncotermsClassification=item.incoterms, ShippingCondition=item.shipping_condition, FreightPerUnit=item.freight_per_unit,
        Plant=item.plant, PurchaseOrderQuantityUnit=item.unit, NetPriceQuantity=item.net_price_qty, DocumentCurrency=item.currency,
        PurchaseRequisition=item.pr_ref, PurchaseRequisitionItem=None if item.pr_item_no is None else str(item.pr_item_no),
        PurchasingDocumentDeletionCode=item.deletion_code,
    ).model_dump(mode="json")


def _purchase_order_record(header: PurchaseOrderHeader) -> dict[str, Any]:
    """E-01 record (the answer of E-14)."""
    return PurchaseOrderRecord(
        PurchaseOrder=header.po_id, Supplier=header.supplier_id, CreationDate=header.created_on, Status=header.status,
        PurchaseOrderType=header.purchase_order_type, CompanyCode=header.company_code, PurchasingOrganization=header.purch_org,
        PurchasingGroup=header.purch_group, DocumentCurrency=header.currency, PurchaseOrderDate=header.created_on,
        CreatedByUser=header.created_by, LastChangeDateTime=header.last_changed_at,
        PurchasingProcessingStatus=header.processing_status, ReleaseIsNotCompleted=header.release_not_completed,
    ).model_dump(mode="json")


def _requisition_record(requisition: PurchaseRequisition) -> dict[str, Any]:
    """E-13 answer: the header with its one item (deep insert)."""
    return PurchaseRequisitionRecord(
        PurchaseRequisition=requisition.pr_id, PurchaseRequisitionType=requisition.pr_type,
        to_PurchaseReqnItem=Results(results=[PurchaseRequisitionItemRecord(
            PurchaseRequisition=requisition.pr_id, PurchaseRequisitionItem=str(requisition.item_no), Material=requisition.material_id,
            Plant=requisition.plant, RequestedQuantity=requisition.qty, BaseUnit=requisition.base_unit,
            DeliveryDate=requisition.delivery_date, FixedSupplier=requisition.fixed_supplier_id,
            PurchasingGroup=requisition.purch_group, PurchaseRequisitionPrice=requisition.price,
            ProcessingStatus=requisition.processing_status)]),
    ).model_dump(mode="json")


def _answer(status: int, content: dict[str, Any], main: SapMessage, details: tuple[SapMessage, ...] = ()) -> tuple[int, dict[str, Any], str]:
    return status, content, sap.sap_message_header(main, details)


@router.patch(PO_SERVICE + "/A_PurchaseOrderScheduleLine({key})")
def change_schedule_line(key: str, body: ScheduleLinePatch, request: Request, idempotency_key: Key, changed_by: ChangedBy = None):
    """E-11 `CHANGE_PO_DATE`."""
    po_id, item_text, line_text = _pairs(key, "Schedule line", "PurchasingDocument", "PurchasingDocumentItem", "ScheduleLine")

    def work(session: Session):
        item_no, line_no = _number(item_text, "Schedule line", key), _number(line_text, "Schedule line", key)
        line = _schedule_line(session, po_id, item_no, line_no)
        _open_item(session, po_id, item_no)
        old, line.confirmed_date = line.confirmed_date, date.fromisoformat(body.ScheduleLineDeliveryDate)
        Journal(session, changed_by).add(
            EINDT, object_key=po_id, table_key=_line_key(po_id, item_no, line_no),
            legacy=("PurchaseOrderScheduleLine", f"{po_id}/{item_no}/{line_no}", "ScheduleLineDeliveryDate"),
            old=old.isoformat(), new=body.ScheduleLineDeliveryDate)
        record = ScheduleLineRecord(PurchasingDocument=po_id, PurchasingDocumentItem=str(item_no), ScheduleLine=str(line_no),
                                    ScheduleLineDeliveryDate=line.confirmed_date)
        return _answer(200, {"d": record.model_dump(mode="json")}, sap.purchase_order_changed(po_id))

    return _perform(request, idempotency_key, body, work)


@router.patch(PO_SERVICE + "/A_PurchaseOrderItem({key})")
def change_item(key: str, body: ItemPatch, request: Request, idempotency_key: Key, changed_by: ChangedBy = None):
    """E-12 `REDUCE_OR_CANCEL_PO` (a quantity of at least 1, or the deletion indicator `L` to cancel) and, v1.11,
    `EXPEDITE_SHIPMENT` (shipping pair)."""
    po_id, item_text = _pairs(key, "Purchase order item", "PurchaseOrder", "PurchaseOrderItem")

    def work(session: Session):
        if body.OrderQuantity == 0:
            raise SapError(400, sap.quantity_not_positive())
        item_no = _number(item_text, "Purchase order item", key)
        item = _open_item(session, po_id, item_no)
        object_id, table_key = f"{po_id}/{item_no}", _item_key(po_id, item_no)
        journal = Journal(session, changed_by)
        if body.OrderQuantity is not None:
            old, item.order_qty = item.order_qty, body.OrderQuantity
            journal.add(MENGE, object_key=po_id, table_key=table_key, legacy=("PurchaseOrderItem", object_id, "OrderQuantity"),
                        old=old, new=body.OrderQuantity)
        elif body.PurchasingDocumentDeletionCode is not None:  # cancel: the quantity stays, the item is flagged (EKPO-LOEKZ)
            old, item.deletion_code, item.status = item.deletion_code, body.PurchasingDocumentDeletionCode, "CANCELLED"
            journal.add(LOEKZ, object_key=po_id, table_key=table_key,
                        legacy=("PurchaseOrderItem", object_id, "PurchasingDocumentDeletionCode"), old=old,
                        new=body.PurchasingDocumentDeletionCode)
        else:
            old_shipping, old_incoterms = item.shipping_condition, item.incoterms
            item.shipping_condition, item.incoterms = body.ShippingCondition, body.IncotermsClassification
            journal.add(ZZSHIPCOND, object_key=po_id, table_key=table_key,
                        legacy=("PurchaseOrderItem", object_id, "ShippingCondition"), old=old_shipping, new=body.ShippingCondition)
            journal.add(INCO1, object_key=po_id, table_key=table_key,
                        legacy=("PurchaseOrderItem", object_id, "IncotermsClassification"), old=old_incoterms,
                        new=body.IncotermsClassification)
        return _answer(200, {"d": _item_record(session, item)}, sap.purchase_order_changed(po_id))

    return _perform(request, idempotency_key, body, work)


@router.post("/sap/opu/odata/sap/API_PURCHASEREQ_PROCESS_SRV/A_PurchaseRequisitionHeader")
def create_requisition(body: RequisitionCreate, request: Request, idempotency_key: Key, changed_by: ChangedBy = None):
    """E-13 `CREATE_PR_PO`, step 1 (deep insert, v1.12)."""
    (item,) = body.to_PurchaseReqnItem.results

    def work(session: Session):
        _required(("PurchaseRequisitionType", body.PurchaseRequisitionType), ("Plant", item.Plant),
                  ("PurchasingGroup", item.PurchasingGroup))
        material = session.get(Material, item.Material)
        if material is None:
            raise SapError(404, sap.does_not_exist("Material", item.Material))
        if session.get(Supplier, item.FixedSupplier) is None:
            raise SapError(404, sap.does_not_exist("Supplier", item.FixedSupplier))
        pr_id = f"{_next_number(session, PurchaseRequisition.pr_id, FIRST_REQUISITION):010d}"
        requisition = PurchaseRequisition(
            pr_id=pr_id, item_no=10, material_id=item.Material, qty=item.RequestedQuantity, fixed_supplier_id=item.FixedSupplier,
            delivery_date=date.fromisoformat(item.DeliveryDate), pr_type=body.PurchaseRequisitionType, plant=item.Plant,
            base_unit=item.BaseUnit if not _blank(item.BaseUnit) else material.base_unit, purch_group=item.PurchasingGroup,
            price=item.PurchaseRequisitionPrice, processing_status="N",
        )
        session.add(requisition)
        Journal(session, changed_by).add(
            EBAN_KEY, object_key=pr_id, table_key=_item_key(pr_id, 10), legacy=("PurchaseRequisition", pr_id, "Created"), old=None,
            new=f"{item.Material} x {item.RequestedQuantity} @ {item.FixedSupplier}")
        return _answer(201, {"d": _requisition_record(requisition)}, sap.requisition_created(pr_id))

    return _perform(request, idempotency_key, body, work)


@router.post(PO_SERVICE + "/A_PurchaseOrder")
def create_purchase_order(body: PurchaseOrderCreate, request: Request, idempotency_key: Key, changed_by: ChangedBy = None):
    """E-14 `CREATE_PR_PO`, step 2 (with items and their schedule line; the PR sits on the item, v1.12)."""
    d0 = _d0(request)
    threshold = request.app.state.settings.mock_s4_po_approval_threshold
    items = body.to_PurchaseOrderItem.results

    def work(session: Session):
        _required(("PurchaseOrderType", body.PurchaseOrderType), ("CompanyCode", body.CompanyCode),
                  ("PurchasingOrganization", body.PurchasingOrganization), ("PurchasingGroup", body.PurchasingGroup),
                  ("Plant", None if any(_blank(i.Plant) for i in items) else "set"))
        if session.get(Supplier, body.Supplier) is None:
            raise SapError(404, sap.does_not_exist("Supplier", body.Supplier))
        materials = {}
        for item in items:
            materials[item.Material] = session.get(Material, item.Material)
            if materials[item.Material] is None:
                raise SapError(404, sap.does_not_exist("Material", item.Material))
        numbers = [int(i.PurchaseOrderItem) if i.PurchaseOrderItem else 10 * (n + 1) for n, i in enumerate(items)]
        if len(set(numbers)) != len(numbers):
            raise SapError(400, sap.validation_failed("PurchaseOrderItem"))
        po_id = str(_next_number(session, PurchaseOrderHeader.po_id, FIRST_PURCHASE_ORDER))
        requisitions: dict[str, PurchaseRequisition] = {}  # the PRs this PO takes over, in the order of the items
        for item in items:
            if item.PurchaseRequisition is None:
                continue
            requisition = session.get(PurchaseRequisition, item.PurchaseRequisition)
            if requisition is None:
                raise SapError(404, sap.does_not_exist("Purchase requisition", item.PurchaseRequisition, "PurchaseRequisition"))
            if requisition.item_no != int(item.PurchaseRequisitionItem):
                raise SapError(404, sap.does_not_exist(
                    "Purchase requisition item", f"{requisition.pr_id}/{sap.item5(item.PurchaseRequisitionItem)}",
                    "PurchaseRequisitionItem"))
            if requisition.processing_status == "B" or requisition.pr_id in requisitions:
                raise SapError(409, sap.requisition_converted(requisition.pr_id, requisition.po_ref or po_id))
            requisitions[requisition.pr_id] = requisition
        value = _net_value(items)
        waits = value >= threshold  # the start condition "total net amount" of a release workflow (B-53)
        header = PurchaseOrderHeader(
            po_id=po_id, supplier_id=body.Supplier, created_on=d0, status="OPEN", purchase_order_type=body.PurchaseOrderType,
            company_code=body.CompanyCode, purch_org=body.PurchasingOrganization, purch_group=body.PurchasingGroup,
            currency=body.DocumentCurrency if not _blank(body.DocumentCurrency) else config.DOCUMENT_CURRENCY,
            created_by=sap_username(changed_by), last_changed_at=datetime.now(UTC),
            processing_status=IN_RELEASE if waits else ACTIVE, release_not_completed=waits)
        session.add(header)
        session.flush()
        for number, item in zip(numbers, items):
            requested = date.fromisoformat(item.RequestedDeliveryDate)
            unit = item.PurchaseOrderQuantityUnit if not _blank(item.PurchaseOrderQuantityUnit) else materials[item.Material].base_unit
            session.add(PurchaseOrderItem(
                po_id=po_id, item_no=number, material_id=item.Material, order_qty=item.OrderQuantity,
                net_price=item.NetPriceAmount, requested_date=requested, status="OPEN", incoterms=item.IncotermsClassification,
                shipping_condition=item.ShippingCondition, freight_per_unit=item.FreightPerUnit, plant=item.Plant, unit=unit,
                net_price_qty=item.NetPriceQuantity or 1, currency=header.currency, pr_ref=item.PurchaseRequisition,
                pr_item_no=None if item.PurchaseRequisitionItem is None else int(item.PurchaseRequisitionItem),
            ))
            session.add(PurchaseOrderScheduleLine(po_id=po_id, item_no=number, line_no=1, confirmed_date=requested))
        journal = Journal(session, changed_by)
        journal.add(EKKO_KEY, object_key=po_id, table_key=po_id, legacy=("PurchaseOrder", po_id, "Created"), old=None,
                    new=f"{body.Supplier}: " + "; ".join(f"{n} {i.Material} x {i.OrderQuantity}" for n, i in zip(numbers, items)))
        for requisition in requisitions.values():
            old_status, requisition.po_ref, requisition.processing_status = requisition.processing_status, po_id, "B"
            journal.add(EBAN_STATU, object_key=requisition.pr_id, table_key=_item_key(requisition.pr_id, requisition.item_no),
                        legacy=("PurchaseRequisition", requisition.pr_id, "Status"), old=old_status, new=requisition.processing_status)
        details = (sap.purchase_order_subject_to_approval(po_id, value),) if waits else ()
        return _answer(201, {"d": _purchase_order_record(header)}, sap.purchase_order_created(po_id), details)

    return _perform(request, idempotency_key, body, work)


@router.post("/sentra-ext/purchase-orders({key})/release")
def release_purchase_order(key: str, body: ReleaseBody, request: Request, idempotency_key: Key, changed_by: ChangedBy = None):
    """E-18 (E.2.4): the release of a PO in release, `03` -> `05`. Not a standard S/4 API: SAP releases from My Inbox or ME29N;
    SENTRA's Approve plays that screen. The key is `'4500000001'`."""
    po_id = key.strip().strip("'")

    def work(session: Session):
        header = session.get(PurchaseOrderHeader, po_id)
        if header is None:
            raise SapError(404, sap.does_not_exist("Purchase order", po_id))
        _not_rejected(header)
        if header.processing_status != IN_RELEASE:
            raise SapError(409, sap.purchase_order_not_waiting(po_id))
        journal = Journal(session, changed_by)
        old, header.processing_status, header.release_not_completed = header.processing_status, RELEASED, False
        header.last_changed_at = datetime.now(UTC)
        journal.add(PROCSTAT, object_key=po_id, table_key=po_id, legacy=("PurchaseOrder", po_id, "PurchasingProcessingStatus"),
                    old=old, new=header.processing_status)
        return _answer(200, {"d": _purchase_order_record(header)}, sap.purchase_order_released(po_id, journal.username))

    return _perform(request, idempotency_key, body, work)


@router.patch("/sap/opu/odata/sap/API_PRODUCTION_ORDER_2_SRV/A_ProductionOrder_2({key})")
def reschedule_production_order(key: str, body: ProductionOrderPatch, request: Request, idempotency_key: Key,
                                changed_by: ChangedBy = None):
    """E-15 `RESCHEDULE_PRODUCTION_ORDER`; the key is `'PRD-03'`."""
    order_id = key.strip().strip("'")

    def work(session: Session):
        order = session.get(ProductionOrder, order_id)
        if order is None:
            raise SapError(404, sap.does_not_exist("Production order", order_id))
        old, order.start_date = order.start_date, date.fromisoformat(body.MfgOrderPlannedStartDate)
        Journal(session, changed_by).add(
            GSTRP, object_key=order_id, table_key=order_id, legacy=("ProductionOrder", order_id, "MfgOrderPlannedStartDate"),
            old=old.isoformat(), new=body.MfgOrderPlannedStartDate)
        record = ProductionOrderRecord(
            ManufacturingOrder=order.order_id, Product=order.product_id, MfgOrderPlannedTotalQty=order.qty,
            MfgOrderPlannedStartDate=order.start_date, SalesOrder=order.sales_order_id, Status=order.status)
        return _answer(200, {"d": record.model_dump(mode="json")}, sap.production_order_saved(order_id))

    return _perform(request, idempotency_key, body, work)


@router.patch("/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrderItem({key})")
def reschedule_sales_order(key: str, body: SalesOrderItemPatch, request: Request, idempotency_key: Key,
                           changed_by: ChangedBy = None):
    """E-16 `RESCHEDULE_SALES_ORDER` (one item, `"10"`, per sales order)."""
    so_id, item_no = _pairs(key, "Sales order item", "SalesOrder", "SalesOrderItem")

    def work(session: Session):
        order = session.get(SalesOrder, so_id)
        if order is None or item_no != "10":
            shown = f"{so_id}/{sap.item5(item_no)}" if item_no.isdigit() else f"{so_id}/{item_no}"
            raise SapError(404, sap.does_not_exist("Sales order item", shown))
        old, order.confirmed_delivery_date = order.confirmed_delivery_date, date.fromisoformat(body.ConfirmedDeliveryDate)
        Journal(session, changed_by).add(
            EDATU, object_key=so_id, table_key=_line_key(so_id, 10, 1), legacy=("SalesOrderItem", f"{so_id}/10", "ConfirmedDeliveryDate"),
            old=old.isoformat(), new=body.ConfirmedDeliveryDate)
        record = SalesOrderItemRecord(
            SalesOrder=so_id, SalesOrderItem="10", CustomerName=order.customer, Material=order.product_id,
            RequestedQuantity=order.qty, RequestedDeliveryDate=order.requested_delivery_date,
            ConfirmedDeliveryDate=order.confirmed_delivery_date)
        return _answer(200, {"d": record.model_dump(mode="json")}, sap.sales_order_saved(so_id))

    return _perform(request, idempotency_key, body, work)


@router.get("/sentra-ext/change-documents", response_model=Collection[ChangeDocumentRecord])
def change_documents(request: Request):
    """E-17: what SENTRA changed, oldest first (`change_number`, then `id`)."""
    with request.app.state.database.session_factory() as session:
        rows = session.scalars(select(ChangeDocument).order_by(ChangeDocument.change_number, ChangeDocument.id)).all()
        return Collection(d=Results(results=[
            ChangeDocumentRecord(
                id=r.id, object_type=r.object_type, object_id=r.object_id, field=r.field, old_value=r.old_value,
                new_value=r.new_value, changed_by=r.changed_by,
                changed_at=r.changed_at.replace(tzinfo=UTC).isoformat(timespec="seconds"), source=r.source,
                change_number=r.change_number, object_class=r.object_class, object_key=r.object_key, table_name=r.table_name,
                table_key=r.table_key, field_name=r.field_name, field_label=r.field_label, change_ind=r.change_ind,
                tcode=r.tcode, username=r.username)
            for r in rows]))
