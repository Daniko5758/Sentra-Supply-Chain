"""IDs created by the application: prefix + 4 digits (`<PREFIX>-NNNN`), `RUN-YYYYMMDD-NNNN` (DATA_MODEL section 1)."""

from sqlalchemy import func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

# Prefixes of the ids made by the application (DATA_MODEL section 1); older tables pass theirs to `next_id` directly.
REQUEST_PREFIX = "REQ"  # supplier_request (v1.6)
MEETING_PREFIX = "MTG"  # meeting (v1.6)
DECISION_PREFIX = "DCN"  # meeting_decision (v1.6)


def next_id(session: Session, column: InstrumentedAttribute, prefix: str, *, width: int = 4) -> str:
    """`<prefix>-<n+1>` where n is the highest number used so far (numbering restarts only after a reset)."""
    last = session.scalar(
        select(column).where(column.like(f"{prefix}-%")).order_by(func.length(column).desc(), column.desc()).limit(1)
    )
    number = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}-{number:0{width}d}"
