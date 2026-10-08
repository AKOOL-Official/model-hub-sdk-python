import httpx
import pytest

from akool_modelhub_sdk import AsyncModelHub, ModelHub, ModelHubError, ProtocolError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [{}, {"status": 401}, {"contract_version": "v1", "key_model_discovery_v1": "true"}]
)
async def test_authenticate_rejects_success_status_without_contract(body):
    async with AsyncModelHub(
        api_key="sk-test",
        base_url="https://example.invalid/api/v1",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
    ) as client:
        with pytest.raises(ProtocolError):
            await client.authenticate()


@pytest.mark.asyncio
async def test_authenticate_never_uses_cached_capabilities_or_legacy_fallback():
    statuses = iter([200, 401, 404])

    def handle(_):
        return httpx.Response(
            next(statuses), json={"contract_version": "v1", "key_model_discovery_v1": True}
        )

    async with AsyncModelHub(
        api_key="sk-test",
        base_url="https://example.invalid/api/v1",
        transport=httpx.MockTransport(handle),
    ) as client:
        assert (await client.capabilities()).contract_version == "v1"
        for status in (401, 404):
            with pytest.raises(ModelHubError) as error:
                await client.authenticate()
            assert error.value.status_code == status


def test_sync_authenticate_public_api():
    with ModelHub(
        api_key="sk-test",
        base_url="https://example.invalid/api/v1",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "contract_version": "v1",
                    "key_model_discovery_v1": True,
                },
            )
        ),
    ) as client:
        assert client.authenticate().key_model_discovery_v1
