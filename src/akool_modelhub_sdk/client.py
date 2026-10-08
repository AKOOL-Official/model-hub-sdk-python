from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import random
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4

import httpx
from pydantic import ValidationError as PydanticValidationError

from .errors import (
    AuthenticationError,
    BillingError,
    ModelHubError,
    NotFoundError,
    PermissionDeniedError,
    ProtocolError,
    RateLimitError,
    SubmissionUnknownError,
    TaskCancelledError,
    TaskFailedError,
    TaskWaitTimeoutError,
    ValidationError,
)
from .schema import convert_schema
from .types import (
    Capabilities,
    Model,
    ModelSchema,
    Page,
    PricePreview,
    Response,
    Task,
    TaskSubmission,
)


def safe_id(value: str, *, model: bool = False) -> str:
    if not isinstance(value, str) or not value or len(value) > (100 if model else 36):
        raise ValueError("Invalid model or task ID")
    decoded = value
    for _ in range(5):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    if (
        ":" in decoded
        or "\\" in decoded
        or "%" in decoded
        or any(ord(c) < 32 or ord(c) == 127 for c in decoded)
        or any(p in ("", ".", "..") for p in decoded.split("/"))
        or (not model and "/" in decoded)
    ):
        raise ValueError("Unsafe model or task ID")
    return "/".join(quote(p, safe="") for p in value.split("/"))


def pagination(skip: int, limit: int) -> dict[str, int]:
    if type(skip) is not int or type(limit) is not int or skip < 0 or not 1 <= limit <= 100:
        raise ValueError("skip must be nonnegative and limit must be between 1 and 100")
    return {"skip": skip, "limit": limit}


def retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, seconds) if math.isfinite(seconds) else None


def response_error(response: httpx.Response) -> ModelHubError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    error = body.get("error", {}) if isinstance(body, dict) else {}
    if not isinstance(error, dict):
        error = {}
    cls = {
        400: ValidationError,
        401: AuthenticationError,
        402: BillingError,
        403: PermissionDeniedError,
        404: NotFoundError,
        422: ValidationError,
        429: RateLimitError,
    }.get(response.status_code, ModelHubError)
    return cls(
        str(error.get("message") or f"Model Hub HTTP {response.status_code}"),
        status_code=response.status_code,
        code=str(error.get("code") or "http_error"),
        request_id=response.headers.get("X-Request-Id") or error.get("request_id"),
        retry_after=retry_after(response.headers.get("Retry-After")),
        details=error.get("details"),
    )


def parse(cls, payload):
    try:
        return cls.model_validate(payload)
    except PydanticValidationError as exc:
        raise ProtocolError("Invalid Model Hub response", code="invalid_response") from exc


class AsyncModelHub:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 90,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        key = api_key if api_key is not None else (os.getenv("AKOOL_MODELHUB_API_KEY"))
        base = base_url if base_url is not None else (os.getenv("AKOOL_MODELHUB_BASE_URL"))
        if not key or not base:
            raise ValueError("AKOOL_MODELHUB_API_KEY and AKOOL_MODELHUB_BASE_URL are required")
        url = urlsplit(base)
        if (
            url.scheme not in ("http", "https")
            or not url.netloc
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "base_url must be an HTTP(S) API root without credentials, query or fragment"
            )
        if (
            not math.isfinite(timeout)
            or timeout <= 0
            or type(max_retries) is not int
            or not 0 <= max_retries <= 10
        ):
            raise ValueError("Invalid timeout or max_retries")
        self.timeout, self.max_retries = timeout, max_retries
        self._http = httpx.AsyncClient(
            base_url=base.rstrip("/") + "/",
            transport=transport,
            headers={
                "Authorization": f"Bearer {key}",
                "User-Agent": "akool-modelhub-sdk-python/0.1.0",
            },
            follow_redirects=False,
        )
        self._cache: dict[str, tuple[float, dict]] = {}
        self.models = AsyncModels(self)
        self.pricing = AsyncPricing(self)
        self.tasks = AsyncTasks(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    async def aclose(self):
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params=None,
        body=None,
        headers=None,
        retry: bool = True,
        deadline: float | None = None,
    ) -> dict:
        deadline = min(deadline or float("inf"), time.monotonic() + self.timeout)
        error: ModelHubError | None = None
        for attempt in range(self.max_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            temporary = False
            try:
                async with asyncio.timeout(remaining):
                    response = await self._http.request(
                        method,
                        path,
                        params={k: v for k, v in (params or {}).items() if v is not None},
                        json=body,
                        headers=headers,
                        timeout=httpx.Timeout(min(60, remaining), connect=min(5, remaining)),
                    )
                if response.is_success:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise ProtocolError(
                            "Non-JSON Model Hub response",
                            code="invalid_response",
                            request_id=response.headers.get("X-Request-Id"),
                        ) from exc
                    if not isinstance(payload, dict):
                        raise ProtocolError("Expected an object response", code="invalid_response")
                    return {**payload, "request_id": response.headers.get("X-Request-Id")}
                error = response_error(response)
                temporary = (
                    response.status_code in (502, 503, 504)
                    or (response.status_code == 409 and error.code == "idempotency_in_progress")
                    or (
                        response.status_code == 429
                        and error.retry_after is not None
                        and error.retry_after <= 60
                        and response.headers.get("X-RateLimit-Scope") != "api_key_model"
                        and "quota" not in error.message.lower()
                    )
                )
            except (httpx.TransportError, TimeoutError) as exc:
                error = ModelHubError(
                    "Model Hub connection failed or timed out", code="transport_error"
                )
                error.__cause__ = exc
                temporary = True
            if not retry or not temporary or attempt == self.max_retries:
                break
            delay = (
                error.retry_after
                if error.retry_after is not None
                else random.uniform(0.25, 0.5) * 2**attempt
            )
            if delay >= deadline - time.monotonic():
                break
            await asyncio.sleep(delay)
        raise error or ModelHubError("Operation deadline exceeded", code="timeout")

    async def _cached(self, path, params, ttl, refresh):
        key = path + json.dumps(params, sort_keys=True)
        entry = self._cache.get(key)
        if not refresh and entry and entry[0] > time.monotonic():
            return copy.deepcopy(entry[1])
        value = await self._request("GET", path, params=params)
        if len(self._cache) >= 128:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = (time.monotonic() + ttl, copy.deepcopy(value))
        return value

    async def capabilities(self, *, refresh: bool = False) -> Capabilities:
        try:
            return parse(Capabilities, await self._cached("client/capabilities", {}, 60, refresh))
        except NotFoundError:
            return Capabilities()

    async def authenticate(self) -> Capabilities:
        """Validate this key against the current contract, without cache or legacy fallback.

        Hosted adapters can use this on every request before exposing tools. A 404
        or an HTTP 200 error envelope must never be treated as valid credentials.
        """
        data = await self._request("GET", "client/capabilities", retry=False)
        if (
            not isinstance(data.get("contract_version"), str)
            or not data["contract_version"]
            or data.get("key_model_discovery_v1") is not True
        ):
            raise ProtocolError("Required backend contract is unavailable")
        return parse(Capabilities, data)

    async def run(
        self, model_id: str, *, input: dict[str, Any], timeout: float = 600, **kwargs
    ) -> Task:
        task = await self.tasks.create(model_id, input=input, **kwargs)
        return await self.tasks.wait(task.task_uuid, timeout=timeout)


class AsyncModels:
    def __init__(self, client):
        self._client = client

    async def list(
        self,
        *,
        skip: int = 0,
        limit: int = 20,
        search: str | None = None,
        task_type: str | None = None,
        tags: list[str] | None = None,
        refresh: bool = False,
    ) -> Page[Model]:
        params = {
            **pagination(skip, limit),
            "search": search,
            "task_type": task_type,
            "tags": ",".join(tags) if tags else None,
        }
        # Recheck authorization on every catalog read. Never fall back to the public catalog.
        data = await self._client._request("GET", "client/models", params=params)
        return parse(Page[Model], {**data, "skip": skip, "limit": limit})

    async def get(self, model_id: str, *, refresh: bool = False) -> Model:
        safe_id(model_id, model=True)
        return parse(
            Model,
            await self._client._request(
                "GET", "client/models/detail", params={"model_id": model_id}
            ),
        )

    async def schema(self, model_id: str, *, refresh: bool = False) -> ModelSchema:
        return convert_schema(await self.get(model_id, refresh=refresh))

    async def documentation(self, model_id: str, *, language: str = "en") -> Response:
        safe_id(model_id, model=True)
        return parse(
            Response,
            await self._client._request(
                "GET",
                "client/models/documentation/lang",
                params={"model_id": model_id, "language": language, "fallback": "true"},
            ),
        )


class AsyncPricing:
    def __init__(self, client):
        self._client = client

    async def get(self, model_id: str) -> Response:
        return parse(
            Response,
            await self._client._request(
                "GET", "pricing/model-pricing/" + safe_id(model_id, model=True)
            ),
        )

    async def preview(
        self, model_id: str, *, input: dict[str, Any], priced_at: str | None = None
    ) -> PricePreview:
        safe_id(model_id, model=True)
        return parse(
            PricePreview,
            await self._client._request(
                "POST",
                "pricing/preview",
                body={"model_id": model_id, "input_params": input, "priced_at": priced_at},
            ),
        )


class AsyncTasks:
    def __init__(self, client):
        self._client = client

    async def create(
        self,
        model_id: str,
        *,
        input: dict[str, Any],
        webhook_url: str | None = None,
        request_id: str | None = None,
    ) -> TaskSubmission:
        path = "run/" + safe_id(model_id, model=True)
        if not isinstance(input, dict):
            raise ValueError("input must be a JSON object")
        request_id = request_id or str(uuid4())
        safe_id(request_id)
        headers = {"X-Request-Id": request_id}
        json.dumps(input, allow_nan=False)  # reject invalid JSON before any paid request
        capabilities = await self._client.capabilities()
        can_retry = (
            capabilities.run_idempotency_v1 and capabilities.idempotency_header == "X-Request-Id"
        )
        try:
            return parse(
                TaskSubmission,
                await self._client._request(
                    "POST",
                    path,
                    body={"input": input, "webhook_url": webhook_url},
                    headers=headers,
                    retry=can_retry,
                ),
            )
        except ModelHubError as exc:
            if (
                exc.status_code is None
                or exc.status_code >= 500
                or exc.code in ("submission_unknown", "idempotency_in_progress")
            ):
                details = exc.details if isinstance(exc.details, dict) else {}
                raise SubmissionUnknownError(
                    "Submission may have succeeded. Recover the original operation; do not create a new one.",
                    candidate_task_uuid=details.get("task_uuid") or request_id,
                    status_code=exc.status_code,
                    code="submission_unknown",
                    request_id=exc.request_id or request_id,
                    details=details,
                    retry_after=exc.retry_after,
                ) from exc
            raise

    async def get(self, task_uuid: str, *, _deadline: float | None = None) -> Task:
        return parse(
            Task,
            await self._client._request(
                "GET", "run/tasks/" + safe_id(task_uuid), deadline=_deadline
            ),
        )

    async def list(
        self,
        *,
        skip: int = 0,
        limit: int = 20,
        model_id: str | None = None,
        status: str | None = None,
    ) -> Page[Task]:
        data = await self._client._request(
            "GET",
            "run/tasks",
            params={**pagination(skip, limit), "model_id": model_id, "status": status},
        )
        return parse(Page[Task], {**data, "skip": skip, "limit": limit})

    async def wait(self, task_uuid: str, *, timeout: float = 600) -> Task:
        safe_id(task_uuid)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline, delay, last = time.monotonic() + timeout, 2.0, None
        try:
            async with asyncio.timeout(timeout):
                while True:
                    last = await self.get(task_uuid, _deadline=deadline)
                    if last.status == "completed":
                        return last
                    if last.status in ("failed", "cancelled"):
                        cls = TaskFailedError if last.status == "failed" else TaskCancelledError
                        raise cls(
                            last.error_message or f"Task {last.status}",
                            task_uuid=task_uuid,
                            task=last,
                            code="task_" + last.status,
                            request_id=last.request_id,
                        )
                    await asyncio.sleep(
                        min(delay * random.uniform(0.8, 1.2), max(0, deadline - time.monotonic()))
                    )
                    delay = min(5, delay * 1.4)
        except TimeoutError:
            raise TaskWaitTimeoutError(
                "Task is still running; resume waiting with its ID",
                task_uuid=task_uuid,
                task=last,
                code="task_wait_timeout",
            ) from None


class _SyncResource:
    def __init__(self, owner, resource):
        self._owner, self._resource = owner, resource

    def _call(self, name, *args, **kwargs):
        return self._owner._call(getattr(self._resource, name), *args, **kwargs)


class Models(_SyncResource):
    def list(
        self,
        *,
        skip: int = 0,
        limit: int = 20,
        search: str | None = None,
        task_type: str | None = None,
        tags: list[str] | None = None,
        refresh: bool = False,
    ) -> Page[Model]:
        return self._call(
            "list",
            skip=skip,
            limit=limit,
            search=search,
            task_type=task_type,
            tags=tags,
            refresh=refresh,
        )

    def get(self, model_id: str, *, refresh: bool = False) -> Model:
        return self._call("get", model_id, refresh=refresh)

    def schema(self, model_id: str, *, refresh: bool = False) -> ModelSchema:
        return self._call("schema", model_id, refresh=refresh)

    def documentation(self, model_id: str, *, language: str = "en") -> Response:
        return self._call("documentation", model_id, language=language)


class Pricing(_SyncResource):
    def get(self, model_id: str) -> Response:
        return self._call("get", model_id)

    def preview(
        self, model_id: str, *, input: dict[str, Any], priced_at: str | None = None
    ) -> PricePreview:
        return self._call("preview", model_id, input=input, priced_at=priced_at)


class Tasks(_SyncResource):
    def create(
        self,
        model_id: str,
        *,
        input: dict[str, Any],
        webhook_url: str | None = None,
        request_id: str | None = None,
    ) -> TaskSubmission:
        return self._call(
            "create", model_id, input=input, webhook_url=webhook_url, request_id=request_id
        )

    def get(self, task_uuid: str) -> Task:
        return self._call("get", task_uuid)

    def list(
        self,
        *,
        skip: int = 0,
        limit: int = 20,
        model_id: str | None = None,
        status: str | None = None,
    ) -> Page[Task]:
        return self._call("list", skip=skip, limit=limit, model_id=model_id, status=status)

    def wait(self, task_uuid: str, *, timeout: float = 600) -> Task:
        return self._call("wait", task_uuid, timeout=timeout)


class ModelHub:
    """Synchronous client owning one event loop and connection pool; use AsyncModelHub in async code."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 90,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._client = AsyncModelHub(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            transport=transport,
        )
        self._runner = asyncio.Runner()
        self.models, self.pricing, self.tasks = (
            Models(self, self._client.models),
            Pricing(self, self._client.pricing),
            Tasks(self, self._client.tasks),
        )
        self._closed = False

    def _call(self, method, *args, **kwargs):
        if self._closed:
            raise RuntimeError("Model Hub client is closed")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("Use AsyncModelHub inside an event loop")
        return self._runner.run(method(*args, **kwargs))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if not self._closed:
            self._call(self._client.aclose)
            self._runner.close()
            self._closed = True

    def capabilities(self, *, refresh: bool = False) -> Capabilities:
        return self._call(self._client.capabilities, refresh=refresh)

    def authenticate(self) -> Capabilities:
        """Validate credentials without using cached capabilities or legacy fallback."""
        return self._call(self._client.authenticate)

    def run(self, model_id: str, *, input: dict[str, Any], **kwargs) -> Task:
        return self._call(self._client.run, model_id, input=input, **kwargs)
