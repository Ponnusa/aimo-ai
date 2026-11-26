# network-api container image.
#
# No Dockerfile existed for this service before -- it was never part of
# CI's docker-build matrix and had never been containerized at all.
# Follows the same uv-workspace install pattern as registry-api/
# buyer-agent/supplier-agent. network-api has no OPA subprocess calls
# (unlike buyer-agent/supplier-agent), so no OPA binary is installed here.
FROM python:3.12-slim AS base

RUN pip install --no-cache-dir uv==0.10.9

WORKDIR /workspace

# Every workspace member must be present for `uv sync --frozen` to
# validate the lockfile, even services this image does not run.
COPY pyproject.toml uv.lock ./
COPY apps/buyer-agent ./apps/buyer-agent
COPY apps/supplier-agent ./apps/supplier-agent
COPY apps/network-api ./apps/network-api
COPY apps/registry-api ./apps/registry-api
COPY apps/buyer-mcp ./apps/buyer-mcp
COPY apps/supplier-mcp ./apps/supplier-mcp
COPY apps/worker ./apps/worker
COPY packages ./packages
COPY industries ./industries

RUN uv sync --frozen --no-dev --package network-api

EXPOSE 8006

CMD ["uv", "run", "--project", ".", "uvicorn", "network_api.app:app", "--host", "0.0.0.0", "--port", "8006"]
