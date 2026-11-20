FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/nexusrelay
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*
COPY packages/mcp-connectors ./packages/mcp-connectors
COPY apps/agent-worker/pyproject.toml ./apps/agent-worker/pyproject.toml
COPY apps/agent-worker/app ./apps/agent-worker/app
COPY apps/agent-worker/alembic.ini ./apps/agent-worker/alembic.ini
COPY apps/agent-worker/migrations ./apps/agent-worker/migrations
RUN pip install --no-cache-dir ./packages/mcp-connectors ./apps/agent-worker
WORKDIR /srv/nexusrelay/apps/agent-worker
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

