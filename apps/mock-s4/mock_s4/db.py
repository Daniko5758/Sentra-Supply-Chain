from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    """Own engine/session factory; no SENTRA tables or business rules."""

    def __init__(self, url: str = "sqlite:///mock_s4.db") -> None:
        self.engine = create_engine(url, connect_args={"check_same_thread": False})
        self.session_factory = sessionmaker(bind=self.engine)

    def create_all(self) -> None:
        from mock_s4 import models  # noqa: F401 -- register tables with Base before startup.

        Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()
