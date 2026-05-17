FROM python:3.11-slim AS builder

WORKDIR /workspace
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install Python dependencies
COPY backend/pyproject.toml backend/uv.lock* ./backend/
COPY backend/src/ ./backend/src/
RUN cd backend && uv pip install --system --compile-bytecode --no-cache .

# Runtime stage
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && groupadd -r appuser && useradd -r -g appuser -u 1000 appuser

# Docker CLI client only (no daemon) — Part B's sandbox manager shells
# out to `docker` against the bsnexus-sandbox-dind sidecar via
# DOCKER_HOST. No socket mount; the app talks TCP to the DinD.
ARG DOCKER_CLI_VERSION=27.5.1
RUN ARCH="$(uname -m)" \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${ARCH}/docker-${DOCKER_CLI_VERSION}.tgz" \
    | tar xz -C /tmp \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker

# Copy Python packages
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# Copy application code
COPY backend/ ./backend/
COPY deploy/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

ENV PYTHONPATH=/app

RUN mkdir -p /app/logs && chown -R appuser:appuser /app
USER appuser

HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=15s \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
# --forwarded-allow-ips=* honours Caddy's X-Forwarded-Proto so trailing-slash
# redirects emit https:// (not http://) Location headers, avoiding browser
# CORS rejection on cross-origin requests from https://nexus.bsvibe.dev.
CMD ["python", "-m", "uvicorn", "backend.src.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
