# API contract snapshot

The Model Hub backend owns the API contract. These versioned snapshots and fixtures
are test inputs; they are not shipped in the SDK package. No sibling checkout is required.
Export an updated snapshot with the backend's `scripts/export_client_openapi.py`
and import the same artifact into both SDK repositories. Review contract changes
and run each SDK's compatibility tests before release.
