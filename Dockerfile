# syntax=docker/dockerfile:1

# ---- dashboard build ---------------------------------------------------------------------------
FROM node:22-alpine AS dashboard
WORKDIR /app/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

# ---- runtime -----------------------------------------------------------------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENTBENCH_DASHBOARD_DIR=/app/dashboard/dist
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[postgres]"

COPY configs ./configs
COPY datasets ./datasets
COPY --from=dashboard /app/dashboard/dist ./dashboard/dist

RUN useradd --create-home --uid 10001 agentbench && mkdir -p /app/runs && chown -R agentbench /app
USER agentbench

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1
ENTRYPOINT ["agentbench"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
