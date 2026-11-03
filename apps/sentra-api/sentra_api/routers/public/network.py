"""EP-05 supplier network with the case highlight (FR-05, GN-04) and EP-06 supplier profile."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from sentra_api.auth import require_user
from sentra_api.deps import get_session
from sentra_api.routers.public.cases import SupplierMessageOut
from sentra_api.services.erp_data import load_erp_data
from sentra_api.services.network import build_network
from sentra_api.services.supplier_profile import supplier_profile

router = APIRouter(prefix="/api", tags=["network"], dependencies=[Depends(require_user)])


class SiteNode(BaseModel):
    id: str
    type: Literal["site"]
    label: str
    tier: Literal["1", "2"]
    kabupaten: str | None
    kabupaten_local: list[str]  # v1.8 (DEC-025): local-script names for CN/JP sites, [] when none
    country: str
    bcp_score: int
    bcp_band: Literal["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK"]  # v1.11
    highlight: Literal["affected", "via_tier2", "confirmed_no_impact", "none"]


class PartNode(BaseModel):
    id: str
    type: Literal["part"]
    label: str
    category: str  # v1.11: material.category
    highlight: bool


class ProductNode(BaseModel):
    id: str
    type: Literal["product"]
    label: str
    highlight: bool


class _Edge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    highlight: bool


class Tier2ToTier1Edge(_Edge):
    type: Literal["tier2_to_tier1"]
    component: str
    component_category: str | None  # v1.11
    buffer_days: int


class SuppliesEdge(_Edge):
    type: Literal["supplies"]


class UsedInEdge(_Edge):
    type: Literal["used_in"]
    qty_per_unit: int


class NetworkOut(BaseModel):
    case_id: str | None
    version: int | None
    nodes: list[Annotated[SiteNode | PartNode | ProductNode, Field(discriminator="type")]]
    edges: list[Annotated[Tier2ToTier1Edge | SuppliesEdge | UsedInEdge, Field(discriminator="type")]]


@router.get("/network", response_model=NetworkOut)
async def network(
    request: Request,
    case_id: str | None = Query(None),
    version: int | None = Query(None, ge=1),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Without `case_id` every highlight is `none` / `false`. `version` (with `case_id`) highlights that snapshot; default latest."""
    return build_network(session, await load_erp_data(request.app), case_id, version)


class BcpAnswers(BaseModel):
    G1: int
    G2: int
    R1: int
    R2: int
    S1: int
    S2: int
    T1: int
    T2: int


class BcpCategories(BaseModel):
    governance: float
    resilience: float
    supply_chain: float
    testing: float


class BcpOut(BaseModel):
    score: int
    band: Literal["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK"]
    categories: BcpCategories
    answers: BcpAnswers
    stated_rto_days: int
    backup_site: bool
    emergency_plan: bool
    safety_stock: bool
    insurance: bool


class BackupOut(BaseModel):
    site: str | None
    ramp_days: int | None
    surcharge_pct: float | None


class Tier2SupplierOut(BaseModel):
    site_id: str
    component: str
    component_category: str | None
    buffer_days: int


class AlternateSourceOut(BaseModel):
    part_id: str
    site_id: str
    price: int
    lead_time_days: int
    max_qty: int
    air_lead_time_days: int | None
    air_freight_per_unit: int | None


class SupplierProfileOut(BaseModel):
    site_id: str
    supplier_name: str
    tier: str
    kabupaten: str | None
    kecamatan: str | None
    country: str
    hazard_zone_flood: bool
    ttr_days: int
    otd_12m: float | None
    backup_site: str | None
    bcp: BcpOut
    backup: BackupOut
    parts_supplied: list[str]
    tier2_suppliers: list[Tier2SupplierOut]
    alternate_sources_for_parts: list[AlternateSourceOut]
    messages: list[SupplierMessageOut]  # EP-14 schema, across cases


@router.get("/suppliers/{site_id}", response_model=SupplierProfileOut)
async def supplier(site_id: str, request: Request, session: Session = Depends(get_session)) -> dict[str, Any]:
    return supplier_profile(session, await load_erp_data(request.app), site_id)
