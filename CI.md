# Independent CI entry point

Run `bash scripts/ci.sh` from a clean checkout. No sibling source repository is needed.
The script installs development dependencies, runs checks and builds release artifacts;
it never uploads packages or deploys a service. Connect it to the selected Git host's CI
when the remote organization is configured. Each repository has independent version tags.

Python projects require Python 3.11+ and uv (CI defaults to Python 3.12).
