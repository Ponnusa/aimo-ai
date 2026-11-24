# supplier-sandbox-erp container image.
#
# Simulates an external supplier ERP/API system over real HTTP, for M7
# pilot-readiness testing. Follows the same uv-workspace install pattern
# as every other AIMO BRAIN service.
FROM python:3.12-slim AS base

RUN pip install --no-cache-dir uv==0.10.9

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY apps/buyer-agent ./apps/buyer-agent
COPY apps/supplier-agent ./apps/supplier-agent
COPY apps/network-api ./apps/network-api
COPY apps/registry-api ./apps/registry-api
COPY apps/buyer-mcp ./apps/buyer-mcp
COPY apps/supplier-mcp ./apps/supplier-mcp
COPY apps/supplier-sandbox-erp ./apps/supplier-sandbox-erp
COPY apps/worker ./apps/worker
COPY packages ./packages
COPY industries ./industries

RUN uv sync --frozen --no-dev --package supplier-sandbox-erp

EXPOSE 8007

CMD ["uv", "run", "--project", ".", "uvicorn", "supplier_sandbox_erp.app:app", "--host", "0.0.0.0", "--port", "8007"]
