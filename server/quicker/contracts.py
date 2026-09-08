from datetime import date as CalendarDate
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol: Literal["chat-completions", "responses", "lm-studio"] = "chat-completions"
    scheme: Literal["http", "https"] = "http"
    host: str = "localhost"
    port: int = Field(default=1234, ge=1, le=65535)
    base_path: str = "/v1"
    model: str = "qwen3.8-27b@q4_k_m"
    api_key: str | None = None
    timeout: int = Field(default=180, ge=10, le=600)
    concurrency: int = Field(default=1, ge=1, le=4)
    image_limit: int = Field(default=2000, ge=800, le=4000)
    reasoning: Literal["default", "off", "on"] = "default"
    output_limit: int = Field(default=12000, ge=2000, le=32000)
    revision: int = 1

    @field_validator("host")
    @classmethod
    def host_only(cls, value):
        if not value or any(c in value for c in "/?#@ \\\r\n"):
            raise ValueError("Enter a hostname or IP address, without a URL scheme or path")
        return value

    @field_validator("base_path")
    @classmethod
    def valid_path(cls, value):
        if not value.startswith("/") or any(c in value for c in "?#\r\n"):
            raise ValueError("Base path must start with / and have no query or fragment")
        return value.rstrip("/")

    @property
    def url(self):
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"{self.scheme}://{host}:{self.port}{self.base_path}"


class PropertyAddress(BaseModel):
    street: str = Field(max_length=200)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=50)
    role: Literal["service", "job", "utility_customer", "mailing", "unknown"] = "unknown"


class ExtractedRow(BaseModel):
    kind: Literal["purchase", "refund", "payment", "fee", "interest", "tax", "invoice", "other"]
    crossed_out: bool = False
    payee: str | None = None
    amount: str | None = None
    currency: str = "USD"
    date: CalendarDate | None = None
    date_basis: Literal["payment", "purchase", "refund", "invoice", "due", "unknown"] = "unknown"
    paid: bool | None = None
    category: str | None = None
    tag: str | None = None
    property: str | None = None
    property_address: PropertyAddress | None = None
    utility_account: str | None = Field(default=None, max_length=100)
    service_customer_id: str | None = Field(default=None, max_length=100)
    billing_customer_id: str | None = Field(default=None, max_length=100)
    invoice_number: str | None = Field(default=None, max_length=100)
    invoice_date: CalendarDate | None = None
    service_period: str | None = Field(default=None, max_length=100)
    amount_basis: Literal[
        "unknown", "service_location_total", "current_charges", "statement_total", "component", "illustration"
    ] = "unknown"
    parcel: str | None = None
    memo: str = ""
    source: str = ""
    page: int = Field(default=1, ge=1)
    warnings: list[str] = Field(default_factory=list)


class Extraction(BaseModel):
    document_type: Literal["credit_card", "tax", "invoice", "utility", "other"]
    transactions: list[ExtractedRow] = Field(default_factory=list, max_length=200)
    warnings: list[str] = Field(default_factory=list)


class ReviewFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payee: str | None = Field(default=None, max_length=300)
    amount_minor: int | None = Field(default=None, ge=-99999999999, le=99999999999)
    date: CalendarDate | None = None
    currency: str = "USD"
    category: str | None = None
    tag: str | None = None
    property: str | None = None
    unit: str = Field(default="unresolved", max_length=100)
    account: str | None = None
    account_override: bool = False
    memo: str = Field(default="", max_length=2000)
    duplicate_acknowledged: bool = False


class ReviewChange(BaseModel):
    id: str
    revision: int
    existing_id: str | None = None
    fields: ReviewFields | None = None


class ReviewAction(BaseModel):
    action: Literal["save", "approve", "remove", "restore", "existing"]
    rows: list[ReviewChange] = Field(min_length=1, max_length=500)
