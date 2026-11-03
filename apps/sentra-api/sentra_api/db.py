from datetime import UTC, datetime

from sqlalchemy import DateTime, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator):
    """Timezone-aware datetime stored as naive UTC (SQLite drops tzinfo); DATA_MODEL section 1."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed; pass a timezone-aware value")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Database:
    """SENTRA database; ERP tables are owned exclusively by mock_s4."""

    def __init__(self, url: str = "sqlite:///sentra.db") -> None:
        self.engine = create_engine(url, connect_args={"check_same_thread": False})
        self.session_factory = sessionmaker(bind=self.engine)

    def create_all(self) -> None:
        from sentra_api import models  # noqa: F401 -- register tables with Base before startup.

        Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()
