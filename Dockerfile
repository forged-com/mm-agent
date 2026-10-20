FROM python:3.12-slim AS build

WORKDIR /build

COPY pyproject.toml ./
COPY src/ src/

RUN python -m venv /opt/mm-agent \
    && /opt/mm-agent/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/mm-agent/bin/pip install --no-cache-dir ".[postgres,mysql,clickhouse]"

FROM build AS test

COPY tests/ tests/
COPY examples/ examples/
COPY contract/ contract/

RUN /opt/mm-agent/bin/pip install --no-cache-dir ".[dev]"

ENV PATH="/opt/mm-agent/bin:$PATH"

CMD ["sh", "-c", "ruff check . && ruff format --check . && mypy && pytest -q"]

FROM python:3.12-slim

COPY --from=build /opt/mm-agent /opt/mm-agent

ENV PATH="/opt/mm-agent/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --create-home --uid 1000 agent
USER agent

WORKDIR /home/agent

ENTRYPOINT ["forgd-mm-agent"]
