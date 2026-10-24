# Build context: the agent/ directory
#   docker build -t pdd/test-agent:latest agent
FROM python:3.12-slim AS build
RUN pip install --no-cache-dir uv==0.11.32
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --system --uid 10001 --create-home --home-dir /home/agent agent \
 && mkdir /data && chown agent:agent /data
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER agent
VOLUME /data
EXPOSE 8000
ENTRYPOINT ["pdd-agent"]
CMD ["--config", "/etc/pdd/test-agent/values.yaml"]
