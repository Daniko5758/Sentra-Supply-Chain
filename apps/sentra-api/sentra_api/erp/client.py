"""HTTP client for mock-s4 (API_CONTRACT E.1). Turns S/4-style records into domain objects."""

import asyncio
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any, Self, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from sentra_api.config import Settings
from sentra_api.erp.domain import (
    BomItem, ErpData, Material, PoLine, Product, ProductionOrder, SalesOrder, Stock,
)
from sentra_api.errors import ApiError


PO_SERVICE = "/sap/opu/odata/sap/API_PURCHASEORDER_PROCESS_SRV"
TIMEOUT_S = 10.0
SEVERITY_TYPES = {"success": "S", "warning": "W", "error": "E", "info": "I"}
# Organisation values of the bodies of E-13/E-14 (contract E, v1.12, DEC-049). The one place in sentra-api that knows them; mock-s4
# keeps its own copy in `mock_s4/config.py` (it has no customizing to read them from).
PURCHASE_ORDER_TYPE = "NB"  # also the type of the purchase requisition
COMPANY_CODE = "LLN1"
PURCHASING_ORGANIZATION = "LLN1"
PURCHASING_GROUP = "P01"
PLANT = "CKR1"
DOCUMENT_CURRENCY = "IDR"
SENTRA_CLASS, SENTRA_NUMBER = "SENTRA", "000"  # the message of a refusal that did not come from the ERP (R-17, no connection)


@dataclass(frozen=True)
class ErpMessage:
    """A message of the ERP in the shape of BAPIRET2 (contract EP-22 v1.12): `type` S/W/E/I, `id` the class, `number` 3 digits."""

    type: str
    id: str
    number: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"type": self.type, "id": self.id, "number": self.number, "message": self.message}


def sentra_message(text: str) -> ErpMessage:
    return ErpMessage("E", SENTRA_CLASS, SENTRA_NUMBER, text)


def _message_from_entry(entry: dict[str, Any]) -> ErpMessage:
    """`{"code": "06/023", "message": ..., "severity": "success"}` -> `ErpMessage("S", "06", "023", ...)`."""
    message_class, _, number = str(entry["code"]).partition("/")
    return ErpMessage(SEVERITY_TYPES[entry["severity"]], message_class, number, str(entry["message"]))


def parse_sap_message(header: str | None) -> tuple[ErpMessage, ...]:
    """The header `sap-message` (E.2.2): the main message, then the `details`. No header, or one that is not the contract: none."""
    if not header:
        return ()
    try:
        payload = json.loads(header)
        return (_message_from_entry(payload), *(_message_from_entry(d) for d in payload.get("details", ())))
    except (ValueError, KeyError, TypeError, AttributeError):
        return ()


class ErpError(ApiError):
    """mock-s4 is unreachable, rejected the request, or returned data the contract does not allow.

    `messages` are what the ERP said (the `errordetails` of an OData error); an error without any is shown as a SENTRA message
    (`E`, `SENTRA`, `000`), so a failed action always has a message to show."""

    def __init__(self, message: str, messages: Iterable[ErpMessage] = ()) -> None:
        super().__init__(502, "UPSTREAM_ERROR", message)
        self.messages: tuple[ErpMessage, ...] = tuple(messages) or (sentra_message(message),)


class Record(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PurchaseOrderItemRecord(Record):
    PurchaseOrder: str
    PurchaseOrderItem: str
    Material: str
    OrderQuantity: int
    NetPriceAmount: int
    Supplier: str
    RequestedDeliveryDate: date
    Status: str
    IncotermsClassification: str = "FCA"
    ShippingCondition: str = "ROAD"
    FreightPerUnit: int = 0


class ScheduleLineRecord(Record):
    PurchasingDocument: str  # v1.12: the key of SAP (v1.11: PurchaseOrder, PurchaseOrderItem)
    PurchasingDocumentItem: str
    ScheduleLine: str
    ScheduleLineDeliveryDate: date


class ProductionOrderRecord(Record):
    ManufacturingOrder: str
    Product: str
    MfgOrderPlannedTotalQty: int
    MfgOrderPlannedStartDate: date
    SalesOrder: str | None
    Status: str


class SalesOrderItemRecord(Record):
    SalesOrder: str
    SalesOrderItem: str
    CustomerName: str
    Material: str
    RequestedQuantity: int
    RequestedDeliveryDate: date
    ConfirmedDeliveryDate: date


class StockRecord(Record):
    Material: str
    MatlWrhsStkQtyInMatlBaseUnit: int


class BomItemRecord(Record):
    Material: str
    BillOfMaterialComponent: str
    BillOfMaterialItemQuantity: int


class ProductRecord(Record):
    Product: str
    ProductDescription: str
    ProductType: str
    ProductCategory: str | None = None
    StandardPrice: int | None = None
    SalesPrice: int | None = None
    MainSupplier: str | None = None
    ExpediteCostPerUnit: int | None = None


R = TypeVar("R", bound=Record)


def _item_no(value: str, entity: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ErpError(f"mock-s4 returned a non-numeric item number for {entity}.") from None


class ErpClient:
    """Async because httpx.ASGITransport (same-process mock-s4) is async-only. Use as `async with`."""

    def __init__(self, base_url: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT_S,
            transport=transport,
        )

    @classmethod
    def from_settings(cls, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> Self:
        if settings.mock_s4_token is None:
            raise ErpError("MOCK_S4_TOKEN is not configured.")
        return cls(settings.mock_s4_base_url, settings.mock_s4_token.get_secret_value(), transport=transport)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _rows(self, path: str, schema: type[R]) -> list[R]:
        try:
            response = await self._http.get(path)
        except httpx.HTTPError as exc:
            raise ErpError("The mock S/4 service is unavailable.") from exc
        if response.status_code != 200:
            raise ErpError(f"The mock S/4 service rejected {path} with HTTP {response.status_code}.")
        try:
            return [schema.model_validate(row) for row in response.json()["d"]["results"]]
        except (ValueError, KeyError, TypeError, ValidationError) as exc:
            raise ErpError(f"The mock S/4 service returned an unexpected payload for {path}.") from exc

    async def po_lines(self) -> list[PoLine]:
        """E-02 items joined with their E-03 schedule line (lowest line number = confirmed date)."""
        items, schedule = await asyncio.gather(
            self._rows(f"{PO_SERVICE}/A_PurchaseOrderItem", PurchaseOrderItemRecord),
            self._rows(f"{PO_SERVICE}/A_PurchaseOrderScheduleLine", ScheduleLineRecord),
        )
        first_line: dict[tuple[str, int], tuple[int, date]] = {}
        for row in schedule:
            key = (row.PurchasingDocument, _item_no(row.PurchasingDocumentItem, "a schedule line"))
            line_no = _item_no(row.ScheduleLine, "a schedule line")
            if key not in first_line or line_no < first_line[key][0]:
                first_line[key] = (line_no, row.ScheduleLineDeliveryDate)
        lines = []
        for row in items:
            item_no = _item_no(row.PurchaseOrderItem, "a purchase order item")
            if (row.PurchaseOrder, item_no) not in first_line:
                raise ErpError(f"Purchase order item {row.PurchaseOrder}/{item_no} has no schedule line.")
            lines.append(PoLine(
                po_id=row.PurchaseOrder, item_no=item_no, material_id=row.Material,
                supplier_id=row.Supplier, qty=row.OrderQuantity, net_price=row.NetPriceAmount,
                requested_date=row.RequestedDeliveryDate,
                confirmed_date=first_line[(row.PurchaseOrder, item_no)][1], status=row.Status,
                incoterms=row.IncotermsClassification, shipping_condition=row.ShippingCondition,
                freight_per_unit=row.FreightPerUnit,
            ))
        return lines

    async def materials_and_products(self) -> tuple[list[Material], list[Product]]:
        """E-09: `ROH` records are parts (Material), `FERT` records are finished goods (Product)."""
        materials, products = [], []
        for row in await self._rows("/sap/opu/odata/sap/API_PRODUCT_SRV/A_Product", ProductRecord):
            if row.ProductType == "ROH":
                if row.ProductCategory is None or row.StandardPrice is None or row.MainSupplier is None:
                    raise ErpError(f"Part {row.Product} is missing category, standard price, or main supplier.")
                materials.append(Material(row.Product, row.ProductDescription, row.ProductCategory,
                                          row.StandardPrice, row.MainSupplier, row.ExpediteCostPerUnit))
            elif row.ProductType == "FERT":
                if row.SalesPrice is None:
                    raise ErpError(f"Product {row.Product} is missing a sales price.")
                products.append(Product(row.Product, row.ProductDescription, row.SalesPrice))
            else:
                raise ErpError(f"Unknown product type {row.ProductType!r} for {row.Product}.")
        return materials, products

    async def bom_items(self) -> list[BomItem]:
        rows = await self._rows("/sap/opu/odata/sap/API_BILL_OF_MATERIAL_SRV/MaterialBOMItem", BomItemRecord)
        return [BomItem(r.Material, r.BillOfMaterialComponent, r.BillOfMaterialItemQuantity) for r in rows]

    async def stocks(self) -> list[Stock]:
        rows = await self._rows("/sap/opu/odata/sap/API_MATERIAL_STOCK_SRV/A_MatlStkInAcctMod", StockRecord)
        return [Stock(r.Material, r.MatlWrhsStkQtyInMatlBaseUnit) for r in rows]

    async def production_orders(self) -> list[ProductionOrder]:
        rows = await self._rows("/sap/opu/odata/sap/API_PRODUCTION_ORDER_2_SRV/A_ProductionOrder_2",
                                ProductionOrderRecord)
        return [ProductionOrder(r.ManufacturingOrder, r.Product, r.MfgOrderPlannedTotalQty,
                                r.MfgOrderPlannedStartDate, r.SalesOrder, r.Status) for r in rows]

    async def sales_orders(self) -> list[SalesOrder]:
        rows = await self._rows("/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrderItem", SalesOrderItemRecord)
        return [SalesOrder(r.SalesOrder, _item_no(r.SalesOrderItem, "a sales order item"), r.CustomerName,
                           r.Material, r.RequestedQuantity, r.RequestedDeliveryDate, r.ConfirmedDeliveryDate)
                for r in rows]

    # --- writes (E-11..E-16, F-12) and the change log (E-17) ---------------------------------------------------------------

    @staticmethod
    def _refusal(response: httpx.Response) -> ErpError:
        """The error of a refused write. OData V2 (E.2.2): the text is `error.message.value` and the messages are the `errordetails`.
        The shape A.3 (401, `404 FEATURE_DISABLED`) and anything else: one SENTRA message with the text."""
        try:
            error = response.json()["error"]
            if isinstance(error["message"], dict):
                text = str(error["message"]["value"])
                details = error.get("innererror", {}).get("errordetails") or [{**error, "message": text, "severity": "error"}]
                return ErpError(text, [_message_from_entry(d) for d in details])
            return ErpError(str(error["message"]))
        except (ValueError, KeyError, TypeError, AttributeError):
            return ErpError(f"The mock S/4 service rejected the change with HTTP {response.status_code}.")

    async def _write(self, method: str, path: str, body: dict[str, Any], *, key: str, by: str) -> tuple[dict[str, Any], tuple[ErpMessage, ...]]:
        """One write with its idempotency key; the same key gives the same answer (and the same messages). Returns the record
        `d` and the messages of the header `sap-message`. A refusal keeps the text and the messages of mock-s4."""
        try:
            response = await self._http.request(method, path, json=body, headers={"X-Idempotency-Key": key, "X-Changed-By": by})
        except httpx.HTTPError as exc:
            raise ErpError("The mock S/4 service is unavailable.") from exc
        if response.status_code not in (200, 201):
            raise self._refusal(response)
        try:
            return response.json()["d"], parse_sap_message(response.headers.get("sap-message"))
        except (ValueError, KeyError, TypeError) as exc:
            raise ErpError("The mock S/4 service returned an unexpected payload for a change.") from exc

    async def change_schedule_line(self, po_id: str, item_no: int, new_date: str, *, key: str, by: str) -> tuple[ErpMessage, ...]:
        """E-11 `CHANGE_PO_DATE`: schedule line 1, the line `po_lines()` reads as the confirmed date."""
        path = (f"{PO_SERVICE}/A_PurchaseOrderScheduleLine(PurchasingDocument='{po_id}',PurchasingDocumentItem='{item_no}',"
                f"ScheduleLine='1')")
        return (await self._write("PATCH", path, {"ScheduleLineDeliveryDate": new_date}, key=key, by=by))[1]

    async def set_order_quantity(self, po_id: str, item_no: int, quantity: int, *, key: str, by: str) -> tuple[ErpMessage, ...]:
        """E-12 `REDUCE_OR_CANCEL_PO`, reduce: `quantity` is at least 1 (v1.12: 0 is ZMOCK_S4/008; cancel with `delete_item`)."""
        path = f"{PO_SERVICE}/A_PurchaseOrderItem(PurchaseOrder='{po_id}',PurchaseOrderItem='{item_no}')"
        return (await self._write("PATCH", path, {"OrderQuantity": quantity}, key=key, by=by))[1]

    async def delete_item(self, po_id: str, item_no: int, *, key: str, by: str) -> tuple[ErpMessage, ...]:
        """E-12 `REDUCE_OR_CANCEL_PO`, cancel (`new_qty = 0`, v1.12): the deletion indicator `L`; the quantity stays in the ERP and
        the line reads as `CANCELLED`."""
        path = f"{PO_SERVICE}/A_PurchaseOrderItem(PurchaseOrder='{po_id}',PurchaseOrderItem='{item_no}')"
        return (await self._write("PATCH", path, {"PurchasingDocumentDeletionCode": "L"}, key=key, by=by))[1]

    async def set_shipping(self, po_id: str, item_no: int, shipping_condition: str, incoterms: str, *, key: str,
                           by: str) -> tuple[ErpMessage, ...]:
        """E-12 v1.11 `EXPEDITE_SHIPMENT`."""
        path = f"{PO_SERVICE}/A_PurchaseOrderItem(PurchaseOrder='{po_id}',PurchaseOrderItem='{item_no}')"
        return (await self._write("PATCH", path, {"ShippingCondition": shipping_condition, "IncotermsClassification": incoterms},
                                  key=key, by=by))[1]

    async def create_requisition(self, material: str, quantity: int, supplier: str, delivery_date: str, price: int = 0, *, key: str,
                                 by: str) -> tuple[str, tuple[ErpMessage, ...]]:
        """E-13 `CREATE_PR_PO` step 1 (deep insert, v1.12); returns the number of the new purchase requisition and the messages.
        `price` is the price per unit of the action; the base unit is left out, mock-s4 takes the one of the material."""
        item = {"Material": material, "Plant": PLANT, "RequestedQuantity": quantity, "DeliveryDate": delivery_date,
                "FixedSupplier": supplier, "PurchasingGroup": PURCHASING_GROUP, "PurchaseRequisitionPrice": price}
        created, messages = await self._write(
            "POST", "/sap/opu/odata/sap/API_PURCHASEREQ_PROCESS_SRV/A_PurchaseRequisitionHeader",
            {"PurchaseRequisitionType": PURCHASE_ORDER_TYPE, "to_PurchaseReqnItem": {"results": [item]}}, key=key, by=by)
        return str(created["PurchaseRequisition"]), messages

    async def create_purchase_order(
        self, requisition: str | None, supplier: str, material: str, quantity: int, price: int, delivery_date: str,
        incoterms: str, shipping_condition: str, freight_per_unit: int, *, key: str, by: str,
    ) -> tuple[str, str, tuple[ErpMessage, ...]]:
        """E-14 `CREATE_PR_PO` step 2 (v1.12): one item with the PR it was made from; returns the number of the new purchase order,
        its `PurchasingProcessingStatus` (B-53: `03` waits for release, `02` is active) and the messages."""
        item = {"Material": material, "Plant": PLANT, "OrderQuantity": quantity, "NetPriceAmount": price, "NetPriceQuantity": 1,
                "RequestedDeliveryDate": delivery_date, "IncotermsClassification": incoterms, "ShippingCondition": shipping_condition,
                "FreightPerUnit": freight_per_unit}
        if requisition is not None:
            item |= {"PurchaseRequisition": requisition, "PurchaseRequisitionItem": "10"}  # the one item of the deep insert
        body = {"PurchaseOrderType": PURCHASE_ORDER_TYPE, "CompanyCode": COMPANY_CODE, "PurchasingOrganization": PURCHASING_ORGANIZATION,
                "PurchasingGroup": PURCHASING_GROUP, "DocumentCurrency": DOCUMENT_CURRENCY, "Supplier": supplier,
                "to_PurchaseOrderItem": {"results": [item]}}
        created, messages = await self._write("POST", f"{PO_SERVICE}/A_PurchaseOrder", body, key=key, by=by)
        try:
            return str(created["PurchaseOrder"]), str(created["PurchasingProcessingStatus"]), messages
        except KeyError as exc:
            raise ErpError("The mock S/4 service returned an unexpected payload for a change.") from exc

    async def release_purchase_order(self, po_id: str, *, key: str, by: str) -> tuple[ErpMessage, ...]:
        """E-18 (v1.12, B-53): the release of a new PO in status `03` (`03` -> `05`), the Approve of SENTRA as the approval screen."""
        return (await self._write("POST", f"/sentra-ext/purchase-orders('{po_id}')/release", {}, key=key, by=by))[1]

    async def set_production_start(self, order_id: str, new_date: str, *, key: str, by: str) -> tuple[ErpMessage, ...]:
        """E-15 `RESCHEDULE_PRODUCTION_ORDER`."""
        path = f"/sap/opu/odata/sap/API_PRODUCTION_ORDER_2_SRV/A_ProductionOrder_2('{order_id}')"
        return (await self._write("PATCH", path, {"MfgOrderPlannedStartDate": new_date}, key=key, by=by))[1]

    async def set_sales_delivery(self, so_id: str, new_date: str, *, key: str, by: str) -> tuple[ErpMessage, ...]:
        """E-16 `RESCHEDULE_SALES_ORDER` (item 10)."""
        path = f"/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrderItem(SalesOrder='{so_id}',SalesOrderItem='10')"
        return (await self._write("PATCH", path, {"ConfirmedDeliveryDate": new_date}, key=key, by=by))[1]

    async def change_documents(self) -> list[dict[str, Any]]:
        """E-17: the `change_document` rows, oldest first."""
        try:
            response = await self._http.get("/sentra-ext/change-documents")
        except httpx.HTTPError as exc:
            raise ErpError("The mock S/4 service is unavailable.") from exc
        if response.status_code != 200:
            raise ErpError(f"The mock S/4 service rejected the change log with HTTP {response.status_code}.")
        try:
            return list(response.json()["d"]["results"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ErpError("The mock S/4 service returned an unexpected payload for the change log.") from exc

    async def load(self) -> ErpData:
        """Read everything the impact engine needs, concurrently."""
        lines, parts_and_products, boms, stocks, production, sales = await asyncio.gather(
            self.po_lines(), self.materials_and_products(), self.bom_items(),
            self.stocks(), self.production_orders(), self.sales_orders(),
        )
        materials, products = parts_and_products
        return ErpData(tuple(lines), tuple(materials), tuple(products), tuple(boms), tuple(stocks),
                       tuple(production), tuple(sales))
