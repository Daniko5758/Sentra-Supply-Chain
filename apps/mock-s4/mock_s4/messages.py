"""SAP messages of the writes (API_CONTRACT E.2.2, DEC-049): the catalog, the `sap-message` header and the OData V2 error.

One builder per row of the catalog; the texts are the ones of the contract, letter for letter. The messages that only later tasks
send (ZMOCK_S4/006, /007, /008, /010, /011, /012) are here too, so the catalog is in one place. `&` of the catalog is filled
with values the ERP knows (document numbers, keys); a value of the request body never goes into a text.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from fastapi.routing import APIRoute

SEVERITY = {"S": "success", "W": "warning", "E": "error", "I": "info"}
CLASS = "ZMOCK_S4"


@dataclass(frozen=True)
class SapMessage:
    type: str  # S, W, E or I
    msg_class: str
    number: str
    text: str
    target: str = ""

    @property
    def code(self) -> str:
        return f"{self.msg_class}/{self.number}"

    @property
    def severity(self) -> str:
        return SEVERITY[self.type]

    def as_entry(self) -> dict[str, str]:
        """The shape of `details` (header) and of `errordetails` (error): no nested `details`."""
        return {"code": self.code, "message": self.text, "severity": self.severity, "target": self.target}


class SapError(Exception):
    """A refusal of a write: the OData V2 error with the main message and, optionally, one more message per field."""

    def __init__(self, status: int, main: SapMessage, details: Sequence[SapMessage] = ()) -> None:
        super().__init__(main.text)
        self.status, self.main, self.details = status, main, tuple(details)


class SapWriteRoute(APIRoute):
    """Marks the routes of the write router: only they answer a rejected request with the OData error (E.2.2)."""


def item5(item_no: int | str) -> str:
    """Item numbers are written with 5 digits in the texts (`00010`), as SAP does."""
    return f"{int(item_no):05d}"


def amount(value: int) -> str:
    """`816,000,000 IDR`: thousands with a comma, then the currency (SAP EN style)."""
    return f"{value:,} IDR"


# --- success -------------------------------------------------------------------------------------------------------------------

def requisition_created(pr: str) -> SapMessage:
    return SapMessage("S", "06", "402", f"Purchase requisition number {pr} created")


def purchase_order_created(po: str) -> SapMessage:
    return SapMessage("S", "06", "017", f"Standard PO created under the number {po}")


def purchase_order_changed(po: str) -> SapMessage:
    return SapMessage("S", "06", "023", f"Standard PO {po} changed")


def production_order_saved(order: str) -> SapMessage:
    return SapMessage("S", "CO", "100", f"Order number {order} saved")


def sales_order_saved(so: str) -> SapMessage:
    return SapMessage("S", "V1", "311", f"Standard Order {so} has been saved.")


def purchase_order_subject_to_approval(po: str, net_value: int) -> SapMessage:
    return SapMessage("W", CLASS, "010", f"Purchase order {po} is subject to approval (net value {amount(net_value)})")


def purchase_order_released(po: str, username: str) -> SapMessage:
    return SapMessage("S", CLASS, "011", f"Purchase order {po} released by {username}")


# --- refusals ------------------------------------------------------------------------------------------------------------------

def does_not_exist(obj: str, key: str, target: str = "") -> SapMessage:
    """ZMOCK_S4/001: `{Object} {key} does not exist`, e.g. `Purchase order item 4500001001/00010 does not exist`."""
    return SapMessage("E", CLASS, "001", f"{obj} {key} does not exist", target)


def validation_failed(target: str = "") -> SapMessage:
    return SapMessage("E", CLASS, "002", "Request validation failed", target)


def item_flagged_for_deletion(po: str, item_no: int | str) -> SapMessage:
    return SapMessage("E", CLASS, "004", f"Item {item5(item_no)} of purchase order {po} is flagged for deletion",
                      "PurchasingDocumentDeletionCode")


def requisition_converted(pr: str, po: str) -> SapMessage:
    return SapMessage("E", CLASS, "005", f"Purchase requisition {pr} has already been converted to PO {po}", "PurchaseRequisition")


def purchase_order_rejected(po: str) -> SapMessage:
    return SapMessage("E", CLASS, "006", f"Purchase order {po} was rejected and cannot be changed")


def field_required(field: str) -> SapMessage:
    return SapMessage("E", CLASS, "007", f"Enter a value for field {field}", field)


def quantity_not_positive() -> SapMessage:
    return SapMessage("E", CLASS, "008", "Quantity must be greater than zero; use the deletion indicator to cancel", "OrderQuantity")


def idempotency_key_used() -> SapMessage:
    return SapMessage("E", CLASS, "009", "Idempotency key was already used for another request", "X-Idempotency-Key")


def purchase_order_not_waiting(po: str) -> SapMessage:
    return SapMessage("E", CLASS, "012", f"Purchase order {po} is not waiting for approval")


# --- the wire forms ------------------------------------------------------------------------------------------------------------

def header_payload(main: SapMessage, details: Sequence[SapMessage] = ()) -> dict[str, Any]:
    """`sap-message`: the main message at the top level, the others in `details`."""
    return {**main.as_entry(), "details": [d.as_entry() for d in details]}


def dump_header(payload: dict[str, Any]) -> str:
    """One ASCII line (an HTTP header cannot carry anything else)."""
    return json.dumps(payload, ensure_ascii=True)


def sap_message_header(main: SapMessage, details: Sequence[SapMessage] = ()) -> str:
    return dump_header(header_payload(main, details))


def odata_error(error: SapError, transaction_id: str) -> dict[str, Any]:
    """The body of a refusal: `error.message` is `{lang, value}`; `errordetails` lists the main message first."""
    return {"error": {
        "code": error.main.code,
        "message": {"lang": "en", "value": error.main.text},
        "innererror": {"transactionid": transaction_id,
                       "errordetails": [m.as_entry() for m in (error.main, *error.details)]},
    }}
