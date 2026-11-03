from datetime import date

from sqlalchemy import Date, delete
from sqlalchemy.orm import Session

from mock_s4.db import Database
from mock_s4.models import MOCK_S4_MODELS
from mock_s4.seed_data import load_source_seed


DELETE_ORDER = tuple(reversed(MOCK_S4_MODELS))
INSERT_COLLECTIONS = {
    "suppliers": MOCK_S4_MODELS[0],
    "materials": MOCK_S4_MODELS[1],
    "products": MOCK_S4_MODELS[2],
    "bom_items": MOCK_S4_MODELS[3],
    "stock": MOCK_S4_MODELS[4],
    "po_headers": MOCK_S4_MODELS[5],
    "po_items": MOCK_S4_MODELS[6],
    "po_schedule_lines": MOCK_S4_MODELS[7],
    "production_orders": MOCK_S4_MODELS[9],
    "sales_orders": MOCK_S4_MODELS[10],
}


def load_mock_seed(d0_date: date, database: Database, *, seed_data: dict | None = None) -> dict:
    """Replace every ERP table with seed data normalized to the supplied D0."""
    bundle = seed_data if seed_data is not None else load_source_seed(d0_date)
    # v1.7 (DEC-049): the unit of a PO item is the base unit of its material (the seed JSON does not carry it).
    base_unit = {row["material_id"]: row["base_unit"] for row in bundle["mock_s4"]["materials"]}
    with Session(database.engine) as session, session.begin():
        for model in DELETE_ORDER:
            session.execute(delete(model))
        for collection, model in INSERT_COLLECTIONS.items():
            date_columns = {
                column.name for column in model.__table__.columns if isinstance(column.type, Date)
            }
            rows = (
                {
                    **row,
                    **{
                        name: date.fromisoformat(row[name])
                        for name in date_columns
                        if isinstance(row.get(name), str)
                    },
                }
                for row in bundle["mock_s4"][collection]
            )
            if collection == "po_items":
                rows = ({**row, "unit": base_unit[row["material_id"]]} for row in rows)
            session.add_all(model(**row) for row in rows)
    return bundle
