# AKOOL Model Hub Python SDK

Distribution: `akool-modelhub-sdk`. Import: `akool_modelhub_sdk`. Python 3.11+.
This repository contains only the Python SDK, its tests and API contract snapshots.
Version 0.1.0 is prepared locally; the package has not yet been published.

After publication:

```bash
python -m pip install akool-modelhub-sdk
```

```python
from akool_modelhub_sdk import ModelHub

with ModelHub(api_key="YOUR_MODEL_HUB_API_KEY", base_url="https://maas.akool.com/api/v1") as client:
    models = client.models.list(search="image", limit=10)
    print(models.items)
    # Choose an authorized model before inspecting its schema.
    if models.items:
        print(client.models.schema(models.items[0].model_id))
```

`AsyncModelHub` exposes the same operations with `async with` and `await`.
Explicit arguments override `AKOOL_MODELHUB_API_KEY` and `AKOOL_MODELHUB_BASE_URL`.
The SDK never reads CLI login files. API roots include `/api/v1` and any gateway prefix.
For hosted adapters, pass the request's key explicitly. `authenticate()` verifies
credentials and the current backend contract without caching or legacy fallback.

Models use the key-authorized `/client/models` endpoints without local catalog caching
or a public-catalog fallback. Inspect `models.schema()` before passing exact model input
fields to `pricing.preview(model_id, input=...)` or `tasks.create(model_id, input=...)`.
Media inputs must already be server-accessible HTTPS URLs; no implicit file upload occurs.

Persist a unique request ID before `tasks.create(..., request_id=saved_id)`.
`tasks.get(task_uuid)` queries once; `tasks.wait(task_uuid, timeout=600)` waits.
`run(model_id, input=..., request_id=saved_id)` submits and waits. On an unknown submission
outcome, recover the original ID and identical input, never a fresh ID. Automatic submit
retries require backend idempotency. A wait timeout or interruption does not cancel the task.
Handle `SubmissionUnknownError`, `TaskFailedError`, `TaskCancelledError` and
`TaskWaitTimeoutError` separately. Output fields depend on the model's schema.

## Development and release

```bash
uv venv
uv pip install --python .venv/bin/python -e . --group dev
.venv/bin/python -m pytest
.venv/bin/ruff check src tests
uv build --out-dir dist
```

Each release owns its version and artifacts. CLI and MCP consume a versioned wheel from
the package registry or a supplied wheel directory, never this repository's source path.
Verify package ownership and registry configuration before the initial upload.
Publish with `uv publish dist/*` after review. No publication is performed by local builds.
See `contracts/README.md` for backend-owned API contracts.

## License

[MIT](LICENSE), Copyright (c) 2026 AKOOL.
