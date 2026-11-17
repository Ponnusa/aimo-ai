# buyer-agent container image.
#
# NOTE: not verified with `docker build` in this environment (Docker is
# not installed here). It follows the same uv-workspace install pattern
# verified via `uv sync --all-packages` locally and in CI, but the image
# build itself should be exercised in CI or by a developer with Docker
# before being relied on for a Railway deployment.
FROM python:3.12-slim AS base

RUN pip install --no-cache-dir uv==0.10.9

WORKDIR /workspace

# Copy the whole workspace: this is a uv workspace, so buyer-agent's
# dependencies on packages/* and industries/food-wholesale are resolved
# as local path dependencies, not published wheels.
COPY pyproject.toml uv.lock ./
COPY apps/buyer-agent ./apps/buyer-agent
COPY apps/supplier-agent ./apps/supplier-agent
COPY apps/network-api ./apps/network-api
COPY apps/registry-api ./apps/registry-api
COPY apps/worker ./apps/worker
COPY packages ./packages
COPY industries ./industries

RUN uv sync --frozen --no-dev --package buyer-agent

EXPOSE 8001

CMD ["uv", "run", "--project", ".", "uvicorn", "buyer_agent.app:app", "--host", "0.0.0.0", "--port", "8001"]
