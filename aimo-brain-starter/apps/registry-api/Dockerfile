# registry-api container image.
#
# Verified to build in CI (.github/workflows/ci.yml docker-build matrix)
# on every push to main. Follows the same uv-workspace install pattern as
# buyer-agent/supplier-agent.
FROM python:3.12-slim AS base

RUN pip install --no-cache-dir uv==0.10.9

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY apps/buyer-agent ./apps/buyer-agent
COPY apps/supplier-agent ./apps/supplier-agent
COPY apps/network-api ./apps/network-api
COPY apps/registry-api ./apps/registry-api
COPY apps/worker ./apps/worker
COPY packages ./packages
COPY industries ./industries

RUN uv sync --frozen --no-dev --package registry-api

EXPOSE 8003

CMD ["uv", "run", "--project", ".", "uvicorn", "registry_api.app:app", "--host", "0.0.0.0", "--port", "8003"]
