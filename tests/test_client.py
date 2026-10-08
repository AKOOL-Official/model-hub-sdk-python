import asyncio
import json
from pathlib import Path

import httpx
import pytest

from akool_modelhub_sdk import (
    AsyncModelHub,
    Model,
    ModelHub,
    ModelHubError,
    SubmissionUnknownError,
    TaskFailedError,
    TaskWaitTimeoutError,
)
from akool_modelhub_sdk.schema import convert_schema

ROOT = Path(__file__).resolve().parents[1]
MODEL = json.loads((ROOT / "contracts/fixtures/model.json").read_text())
TASK = {
    "task_uuid": "task-1",
    "model_id": "example/video",
    "status": "completed",
    "output": {"video_url": "https://example.com/result.mp4", "nested_key": {"a_b": 2}},
}


def client(handler, **kwargs):
    return AsyncModelHub(
        api_key="sk-test",
        base_url="https://example.com/gateway/api/v1",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_gateway_prefix_payload_and_response_keys():
    requests = []

    def handle(req):
        if req.url.path.endswith("capabilities"):
            return httpx.Response(404)
        requests.append(req)
        if req.method == "POST":
            assert json.loads(req.content)["input"] == {"keep_key": 1}
            return httpx.Response(200, json={"task_uuid": "task-1", "status": "pending"})
        return httpx.Response(200, json=TASK, headers={"X-Request-Id": "read-request"})

    async with client(handle) as sdk:
        created = await sdk.tasks.create("example/video model", input={"keep_key": 1})
        result = await sdk.tasks.wait(created.task_uuid)
    assert requests[0].url.raw_path == b"/gateway/api/v1/run/example/video%20model"
    assert all(r.headers["Authorization"] == "Bearer sk-test" for r in requests)
    assert result.output["nested_key"] == {"a_b": 2}
    assert result.request_id == "read-request"


def test_sync_client_retains_connection_lifecycle():
    with ModelHub(
        api_key="sk-test",
        base_url="https://example.com/api/v1",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=TASK)),
    ) as sdk:
        assert sdk.tasks.get("task-1").status == "completed"
        assert sdk.tasks.get("task-1").output == TASK["output"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_id", ["../x", "%2e%2e/x", "%252e%252e/x", "https://bad/x", "a\\b", "a//b"]
)
async def test_path_attacks_never_send_credentials(model_id):
    async with client(lambda _: pytest.fail("request was sent")) as sdk:
        with pytest.raises(ValueError):
            await sdk.tasks.create(model_id, input={})


@pytest.mark.asyncio
async def test_non_idempotent_creation_never_retries_and_retains_candidate():
    calls = []

    def handle(req):
        if req.url.path.endswith("capabilities"):
            return httpx.Response(404)
        calls.append(req)
        raise httpx.ReadTimeout("lost response")

    async with client(handle) as sdk:
        with pytest.raises(SubmissionUnknownError) as caught:
            await sdk.tasks.create("example/video", input={}, request_id="original")
    assert caught.value.candidate_task_uuid == "original"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_idempotent_retry_uses_identical_body_and_key(monkeypatch):
    calls = []

    async def no_sleep(_):
        pass

    monkeypatch.setattr("akool_modelhub_sdk.client.asyncio.sleep", no_sleep)

    def handle(req):
        if req.url.path.endswith("capabilities"):
            return httpx.Response(
                200, json={"run_idempotency_v1": True, "idempotency_header": "X-Request-Id"}
            )
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"task_uuid": "server-task", "status": "pending"})

    async with client(handle) as sdk:
        result = await sdk.tasks.create(
            "example/video", input={"prompt": "x"}, request_id="order-1"
        )
    assert result.task_uuid == "server-task"
    assert len(calls) == 2
    assert calls[0].content == calls[1].content
    assert [r.headers["X-Request-Id"] for r in calls] == ["order-1", "order-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 503])
async def test_capabilities_fail_closed(status):
    def handle(req):
        assert req.method == "GET"
        return httpx.Response(status)

    async with client(handle, max_retries=0) as sdk:
        with pytest.raises(ModelHubError):
            await sdk.tasks.create("m", input={}, request_id="k")


@pytest.mark.asyncio
async def test_failed_task_and_timeout_preserve_task_id():
    async with client(lambda _: httpx.Response(200, json={**TASK, "status": "failed"})) as sdk:
        with pytest.raises(TaskFailedError) as caught:
            await sdk.tasks.wait("task-1")
        assert caught.value.task_uuid == "task-1"
    async with client(
        lambda _: httpx.Response(200, json={**TASK, "status": "a-future-state"})
    ) as sdk:
        with pytest.raises(TaskWaitTimeoutError) as caught:
            await sdk.tasks.wait("task-1", timeout=0.01)
        assert caught.value.task.status == "a-future-state"


@pytest.mark.asyncio
async def test_wait_deadline_includes_http_time():
    async def slow(_):
        await asyncio.sleep(10)
        return httpx.Response(200, json=TASK)

    async with client(slow) as sdk:
        start = asyncio.get_running_loop().time()
        with pytest.raises(TaskWaitTimeoutError):
            await sdk.tasks.wait("task-1", timeout=0.02)
        assert asyncio.get_running_loop().time() - start < 0.2


@pytest.mark.asyncio
async def test_authenticated_models_are_rechecked_and_price_payload_is_unchanged():
    calls = []

    def handle(req):
        calls.append(req)
        if req.url.path.endswith("preview"):
            assert json.loads(req.content)["input_params"] == {"duration": 5}
            return httpx.Response(
                200, json={"list_price": 2, "discount_rate": 0.5, "final_price": 1, "you_save": 1}
            )
        return httpx.Response(200, json=MODEL)

    async with client(handle) as sdk:
        first = await sdk.models.get(MODEL["model_id"])
        first.parameters.clear()
        assert (await sdk.models.get(MODEL["model_id"])).parameters
        assert (await sdk.pricing.preview("m", input={"duration": 5})).final_price == 1
    assert len(calls) == 3
    assert [r.url.path for r in calls[:2]] == ["/gateway/api/v1/client/models/detail"] * 2


@pytest.mark.asyncio
async def test_model_lists_and_private_documentation_use_key_scoped_endpoints():
    calls = []

    def handle(req):
        calls.append(req)
        assert req.headers["Authorization"] == "Bearer sk-test"
        if req.url.path.endswith("/client/models"):
            return httpx.Response(200, json={"total": 1, "items": [MODEL]})
        if req.url.path.endswith("/client/models/documentation/lang"):
            return httpx.Response(200, json={"content": "Private model instructions"})
        pytest.fail("Public catalog must never be called")

    async with client(handle) as sdk:
        for _ in range(2):
            assert (await sdk.models.list(search="video", limit=1)).total == 1
        assert (
            await sdk.models.documentation("private-model")
        ).content == "Private model instructions"
    assert len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_scoped_discovery_never_falls_back_to_public(status):
    calls = []

    def handle(req):
        calls.append(req)
        return httpx.Response(status)

    async with client(handle) as sdk:
        with pytest.raises(ModelHubError):
            await sdk.models.list()
    assert len(calls) == 1
    assert calls[0].url.path == "/gateway/api/v1/client/models"


@pytest.mark.asyncio
async def test_model_detail_is_not_reused_after_permission_revocation():
    calls = []

    def handle(req):
        calls.append(req)
        return httpx.Response(200, json=MODEL) if len(calls) == 1 else httpx.Response(404)

    async with client(handle) as sdk:
        await sdk.models.get(MODEL["model_id"])
        with pytest.raises(ModelHubError):
            await sdk.models.get(MODEL["model_id"])
    assert len(calls) == 2


def test_schema_shared_fixture():
    schema = convert_schema(Model.model_validate(MODEL))
    props = schema.input_schema["properties"]
    assert props["enabled"]["enum"] == [True, False]
    assert props["duration"]["enum"] == [5, 10]
    assert props["advanced"]["default"] == {"keep_this_key": True}
    assert "optional" not in schema.input_schema["required"]
    assert len(schema.warnings) == 2
    expected = json.loads((ROOT / "contracts/fixtures/schema.json").read_text())
    assert schema.model_dump(exclude={"request_id"}) == expected
