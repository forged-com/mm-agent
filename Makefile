ENV_NAME := forgd-mm-agent
RUN := conda run --live-stream -n $(ENV_NAME)

.PHONY: install lint typecheck test conform check ci

install:
	@conda env create --file=environment.yml -n $(ENV_NAME) || \
	conda env update --file=environment.yml -n $(ENV_NAME) --prune

lint:
	$(RUN) ruff check . && $(RUN) ruff format --check .

typecheck:
	$(RUN) mypy

test:
	$(RUN) pytest -q

check: lint typecheck test

conform:
	docker compose -f tests/compose.yaml up -d --wait
	MM_AGENT_CONFORM_POSTGRES_DSN=postgresql://golden:golden@localhost:5432/golden \
	MM_AGENT_CONFORM_MYSQL_DSN=mysql+pymysql://golden:golden@localhost:3306/golden \
	MM_AGENT_CONFORM_CLICKHOUSE_DSN=clickhousedb://golden:golden@localhost:8123/golden \
	$(RUN) pytest -q tests/conformance

ci:
	docker compose -f tests/compose.yaml run --build --rm tests
	docker build -t ghcr.io/forged-com/mm-agent:ci .
	docker run --rm ghcr.io/forged-com/mm-agent:ci --help
