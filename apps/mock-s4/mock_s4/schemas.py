"""Public S/4 field names from API_CONTRACT E.1 (not database column names)."""

from datetime import UTC, date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, field_validator


Row = TypeVar("Row", bound=BaseModel)


class Results(BaseModel, Generic[Row]):
    results: list[Row]


class Collection(BaseModel, Generic[Row]):
    d: Results[Row]


class PurchaseOrderRecord(BaseModel):
    PurchaseOrder: str
    Supplier: str
    CreationDate: date
    Status: str
    # v1.12 (B-51)
    PurchaseOrderType: str
    CompanyCode: str
    PurchasingOrganization: str
    PurchasingGroup: str
    DocumentCurrency: str
    PurchaseOrderDate: date
    CreatedByUser: str
    LastChangeDateTime: str | None  # ISO 8601 UTC, null for the seed
    # v1.12 (B-53)
    PurchasingProcessingStatus: str  # 02 / 03 / 05 / 08
    ReleaseIsNotCompleted: bool

    @field_validator("LastChangeDateTime", mode="before")
    @classmethod
    def _iso_utc(cls, value):
        """The database returns a datetime (UTC, without zone in SQLite); the API shows `2026-10-31T10:12:05+00:00` like E-17."""
        if isinstance(value, datetime):
            value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
            return value.isoformat(timespec="seconds")
        return value


class PurchaseOrderItemRecord(BaseModel):
    PurchaseOrder: str
    PurchaseOrderItem: str
    Material: str
    OrderQuantity: int
    NetPriceAmount: int
    Supplier: str
    RequestedDeliveryDate: date
    Status: str
    IncotermsClassification: str
    ShippingCondition: str
    FreightPerUnit: int
    # v1.12 (B-51)
    Plant: str
    PurchaseOrderQuantityUnit: str
    NetPriceQuantity: int
    DocumentCurrency: str
    PurchaseRequisition: str | None
    PurchaseRequisitionItem: str | None
    PurchasingDocumentDeletionCode: str  # v1.12 (B-52): "" or "L"


class ScheduleLineRecord(BaseModel):
    PurchasingDocument: str  # v1.12: the key of SAP (v1.11: PurchaseOrder, PurchaseOrderItem)
    PurchasingDocumentItem: str
    ScheduleLine: str
    ScheduleLineDeliveryDate: date


class ProductionOrderRecord(BaseModel):
    ManufacturingOrder: str
    Product: str
    MfgOrderPlannedTotalQty: int
    MfgOrderPlannedStartDate: date
    SalesOrder: str | None
    Status: str


class SalesOrderItemRecord(BaseModel):
    SalesOrder: str
    SalesOrderItem: Literal["10"]
    CustomerName: str
    Material: str
    RequestedQuantity: int
    RequestedDeliveryDate: date
    ConfirmedDeliveryDate: date


class StockRecord(BaseModel):
    Material: str
    Plant: str
    MatlWrhsStkQtyInMatlBaseUnit: int


class BomItemRecord(BaseModel):
    Material: str
    BillOfMaterialComponent: str
    BillOfMaterialItemQuantity: int


class SupplierRecord(BaseModel):
    Supplier: str
    SupplierName: str
    Country: str


class PurchaseRequisitionItemRecord(BaseModel):
    PurchaseRequisition: str
    PurchaseRequisitionItem: str
    Material: str
    Plant: str
    RequestedQuantity: int
    BaseUnit: str
    DeliveryDate: date
    FixedSupplier: str
    PurchasingGroup: str
    PurchaseRequisitionPrice: int
    ProcessingStatus: str  # N -> B


class PurchaseRequisitionRecord(BaseModel):
    """E-13 (v1.12): the deep insert answers with the header and its one item."""

    PurchaseRequisition: str
    PurchaseRequisitionType: str
    to_PurchaseReqnItem: Results[PurchaseRequisitionItemRecord]


class ChangeDocumentRecord(BaseModel):
    """E-17: `changed_at` is ISO 8601 in UTC (`+00:00`); `sentra-api` shows it in +07:00. Oldest first: `change_number`, then `id`."""

    id: int
    object_type: str
    object_id: str
    field: str
    old_value: str | None
    new_value: str | None
    changed_by: str
    changed_at: str
    source: str
    # v1.12 (DEC-049, E.2.3): the shape of CDHDR/CDPOS.
    change_number: str
    object_class: str
    object_key: str
    table_name: str
    table_key: str
    field_name: str
    field_label: str
    change_ind: str
    tcode: str
    username: str


class ProductRecord(BaseModel):
    Product: str
    ProductDescription: str
    ProductType: Literal["FERT", "ROH"]
    ProductCategory: str | None
    StandardPrice: int | None
    SalesPrice: int | None
    MainSupplier: str | None
    ExpediteCostPerUnit: int | None
