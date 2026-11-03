from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    mock_s4_token: SecretStr | None = None
    # E-11..E-17 exist only for F-12 (mock_s4 CLAUDE.md rule 5); sentra-api passes its own FEATURE_ERP_EXECUTION.
    feature_erp_execution: bool = False
    # B-53 (DEC-049): a new PO whose net value (IDR) reaches this amount waits for release (`03`); below it the PO is active (`02`).
    # ERP configuration (the start condition of a release workflow), not a SENTRA rule.
    mock_s4_po_approval_threshold: int = 100_000_000


# Organisation values of the ERP (API_CONTRACT E, v1.12, DEC-049). One set for the whole demo; they are customizing of this mock,
# not seed data. The values a request sends are only checked for "not empty", never against these constants.
PURCHASE_ORDER_TYPE = "NB"  # standard PO; also the type of a standard PR
COMPANY_CODE = "LLN1"
PURCHASING_ORGANIZATION = "LLN1"
PURCHASING_GROUP = "P01"
PLANT = "CKR1"
DOCUMENT_CURRENCY = "IDR"
SEED_CREATED_BY = "BUYER"  # `CreatedByUser` of the purchase orders of the seed
