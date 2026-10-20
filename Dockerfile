# sre-agent rapor servisi. Derleme: make image-build (linux/amd64).
FROM ghcr.io/astral-sh/uv:0.12.19 AS uv

FROM python:3.12.14-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12.14-slim
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Domain paketleri image'da; hangilerinin yükleneceğini yapılandırma belirler (ADR-021).
COPY packs ./packs
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["python", "-m", "sre_agent.service"]
CMD ["--config", "/etc/sre-agent/config.yaml", "--base", "/app"]
