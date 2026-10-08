from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictBool

T = TypeVar("T")


class Response(BaseModel):
    model_config = ConfigDict(extra="allow")
    request_id: str | None = None


class Model(Response):
    model_id: str
    name: Any = None
    description: Any = None
    task_type: str | None = None
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    examples: list[dict[str, Any]] = Field(default_factory=list)


class ModelSchema(Response):
    model_id: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    schema_hash: str
    warnings: list[str] = Field(default_factory=list)


class TaskSubmission(Response):
    task_uuid: str
    status: str
    estimated_time: int | None = None
    amount: float | None = None


class Task(TaskSubmission):
    model_id: str
    output: dict[str, Any] | None = None
    error_message: str | None = None
    created_at: str | None = None
    completed_at: str | None = None


class Page(Response, Generic[T]):
    items: list[T]
    total: int
    skip: int
    limit: int


class PricePreview(Response):
    list_price: float
    discount_rate: float
    final_price: float
    discount_reason: str | None = None
    you_save: float
    pricing_window: str | None = None


class Capabilities(Response):
    contract_version: str | None = None
    run_idempotency_v1: StrictBool = False
    idempotency_header: str | None = None
    task_read_api_key_scope: bool = False
    uploads_api_key: bool = False
    price_preview_prepared_input: bool = False
    key_model_discovery_v1: bool = False
