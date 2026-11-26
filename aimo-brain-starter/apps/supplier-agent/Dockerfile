# supplier-agent container image.
#
# Verified to build in CI (.github/workflows/ci.yml docker-build matrix)
# on every push to main.
FROM python:3.12-slim AS base

RUN pip install --no-cache-dir uv==0.10.9

# OPA binary for subprocess-based policy decisions (ADR-011). CI installs
# this separately (.github/workflows/ci.yml) but a deployed image must
# carry it itself. python:3.12-slim has no curl by default.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -sL -o /usr/local/bin/opa https://openpolicyagent.org/downloads/latest/opa_linux_amd64_static \
    && chmod +x /usr/local/bin/opa \
    && opa version

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

RUN uv sync --frozen --no-dev --package supplier-agent

EXPOSE 8002

CMD ["uv", "run", "--project", ".", "uvicorn", "supplier_agent.app:app", "--host", "0.0.0.0", "--port", "8002"]
