"""Optional feature routers: F-10 (EP-25), F-12 (EP-21..EP-23), and F-07 (EP-20, EP-30)."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, StrictStr
from sqlalchemy.orm import Session

from sentra_api.auth import require_user
from sentra_api.deps import get_session
from sentra_api.flags import require_flag
from sentra_api.routers.public.approvals import router as approvals
from sentra_api.routers.public.meeting_room import router as meeting_room
from sentra_api.services import whatif_service
from sentra_api.services.erp_data import load_erp_data
from sentra_api.services.whatif_service import WhatIfResponse

whatif = APIRouter(prefix="/api/whatif", tags=["what-if [F-10]"],
                   dependencies=[Depends(require_flag("FEATURE_WHATIF")), Depends(require_user)])


class WhatIfRequest(BaseModel):
    location: StrictStr


@whatif.get("/locations", response_model=list[str], summary="EP-25 Locations for the what-if")
def locations(session: Session = Depends(get_session)) -> list[str]:
    return whatif_service.locations(session)


@whatif.post("", response_model=WhatIfResponse, summary="EP-25 Run a what-if")
async def run_what_if(body: WhatIfRequest, request: Request, session: Session = Depends(get_session)) -> dict:
    return whatif_service.run_what_if(session, await load_erp_data(request.app), body.location,
                                     whatif_service.simulation_d0(request.app))
