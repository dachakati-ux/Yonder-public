.PHONY: bootstrap validate-config test test-integration test-e2e test-all gateway worker console console-test console-build inspect-host smoke lint migrate verify-migrations verify verify-offline-scripts compose-up compose-down compose-persistence compose-control-plane compose-worker offline-pack fetch-system-rpms fetch-exfat-rpms

UV ?= uv
NODE_BIN := $(CURDIR)/.tools/node/bin
export PATH := $(NODE_BIN):$(PATH)
export YONDER_CONFIG_FILE ?= $(CURDIR)/config/environments/development.yaml
export NEXT_PUBLIC_YONDER_GATEWAY_URL ?= http://127.0.0.1:8080

bootstrap:
	$(UV) sync --all-packages --group dev
	cp -n .env.example .env || true
	mkdir -p data
	@if command -v npm >/dev/null 2>&1; then cd apps/console && npm install; else echo "npm not found; skip console install"; fi
	@echo "Bootstrap complete. Development security mode config: $(YONDER_CONFIG_FILE)"

validate-config:
	$(UV) run python scripts/validate-config.py

test:
	cd apps/gateway && $(UV) run pytest -q
	cd apps/worker && $(UV) run pytest -q

test-integration:
	$(UV) run pytest -q tests/integration

test-e2e:
	$(UV) run pytest -q tests/end-to-end

test-all: test test-integration test-e2e

console-test:
	cd apps/console && npm test

console-build:
	cd apps/console && npm run build

verify-migrations:
	bash scripts/verify-migrations.sh

verify-offline-scripts:
	bash scripts/verify-offline-scripts.sh

verify: validate-config lint test-all verify-migrations verify-offline-scripts console-test console-build

migrate:
	mkdir -p data
	$(UV) run alembic -c database/alembic.ini upgrade head

compose-up:
	docker compose up --build

compose-down:
	docker compose down

compose-persistence:
	docker compose -f compose.yaml -f compose.persistence.yaml --profile persistence up --build

compose-control-plane:
	docker compose -f compose.control-plane.yaml --env-file .env.control-plane up --build

compose-worker:
	docker compose -f compose.worker.yaml --env-file .env.worker up --build

offline-pack:
	bash packaging/offline/build-usb-pack.sh

fetch-system-rpms:
	bash packaging/offline/fetch-system-rpms.sh

fetch-exfat-rpms:
	bash packaging/offline/fetch-exfat-rpms.sh

gateway:
	$(UV) run --package yonder-gateway yonder-gateway

worker:
	$(UV) run --package yonder-worker yonder-worker

console:
	cd apps/console && npm run dev

inspect-host:
	bash scripts/inspect-host.sh

smoke:
	bash scripts/smoke-test.sh

lint:
	$(UV) run ruff check packages apps/gateway apps/worker scripts tests
