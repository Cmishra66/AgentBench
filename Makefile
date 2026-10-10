.PHONY: install dev test lint format dashboard e2e demo serve baseline gate docker clean

install:            ## Install AgentBench and dev tools
	pip install -e ".[dev]"

dashboard:          ## Build the React dashboard
	cd dashboard && npm ci && npm run build

dev: install dashboard

test:               ## Unit and integration tests (offline, ~10s)
	AGENTBENCH_MOCK_TIME_SCALE=0 pytest

benchmark:          ## Every benchmark case as a pytest test, with JUnit output
	AGENTBENCH_MOCK_TIME_SCALE=0 pytest benchmarks/ --ab-target mock-strong --junitxml=benchmark-junit.xml

lint:
	ruff check src tests benchmarks
	ruff format --check src tests benchmarks

format:
	ruff format src tests benchmarks
	ruff check --fix src tests benchmarks

e2e:                ## Playwright UI + API tests (builds the dashboard first)
	cd e2e && npm ci && npx playwright install chromium && npx playwright test

demo:               ## Baseline, regressed candidate, gate, isolation sweep, load test
	agentbench demo

serve:              ## Dashboard + API on http://127.0.0.1:8000
	agentbench serve

baseline:           ## Refresh the committed CI baseline after an intentional change
	agentbench run -c configs/ci.yaml --label main --no-store --json baselines/main.full.json
	agentbench baseline export baselines/main.full.json --out baselines/main.json
	rm -f baselines/main.full.json

gate:               ## What CI does on a pull request
	agentbench run -c configs/ci.yaml --label candidate --baseline baselines/main.json

docker:
	docker compose up --build

clean:
	rm -rf runs agentbench.db .agentbench dashboard/dist e2e/.data
