# Verdify — Development Commands
# Usage: make <target>
SHELL := /bin/bash
# Tooling venv: prefer a repo-local .venv (laptop / agent-pod hosts), fall back
# to the legacy greenhouse-VM path only if it exists, and otherwise fail with a
# clear `make setup` hint instead of trying a dead absolute path.
# Override explicitly with `make VENV=/path/to/venv <target>`.
LEGACY_VENV := /srv/greenhouse/.venv
VENV ?= $(if $(wildcard .venv/bin/python),.venv,$(if $(wildcard $(LEGACY_VENV)/bin/python),$(LEGACY_VENV),.venv))
PYTHON := $(VENV)/bin/python
PYTEST := $(PYTHON) -m pytest
RUFF := $(VENV)/bin/ruff
ESPHOME := $(VENV)/bin/esphome
BOOTSTRAP_PYTHON ?=
BOOTSTRAP_EXTRAS ?= dev,api,planner
ESP32_DEVICE ?= 192.168.10.111
QUIET_MINUTES ?= 30
IRRIGATION_FEEDBACK_TIMEOUT ?= 1800
IRRIGATION_FEEDBACK_INTERVAL ?= 60
IRRIGATION_MQTT_LIVE_TIMEOUT ?= 75
IRRIGATION_FIELD_WATCH_MQTT_TIMEOUT ?= 5
IRRIGATION_FEEDBACK_PROOF ?= /srv/verdify/state/irrigation-feedback-proof.json
IRRIGATION_FIELD_WATCH_PROOF ?= /srv/verdify/state/irrigation-field-watch-proof.txt
IRRIGATION_DISCOVERY_PROOF ?= /srv/verdify/state/irrigation-discovery-proof.txt
IRRIGATION_FINALIZER_PROOF ?= /srv/verdify/state/irrigation-finalizer-proof.txt
IRRIGATION_FINALIZER_DRY_RUN_PROOF ?= /srv/verdify/state/irrigation-finalizer-dry-run-proof.txt
IRRIGATION_WORK_ORDER_PROOF ?= /srv/verdify/state/irrigation-work-order.txt
IRRIGATION_FIELD_SENSOR_HEALTH_PROOF ?= /srv/verdify/state/irrigation-field-sensor-health-proof.txt
IRRIGATION_SENSOR_HEALTH_PROOF ?= /srv/verdify/state/irrigation-sensor-health-proof.txt
IRRIGATION_STACK_PROOF ?= /srv/verdify/state/irrigation-stack-proof.txt
IRRIGATION_MIGRATION_PROOF ?= /srv/verdify/state/irrigation-migration-proof.txt
IRRIGATION_COMPLETION_AUDIT_PROOF ?= /srv/verdify/state/irrigation-completion-audit.json
IRRIGATION_MQTT_HOST ?= 192.168.30.107
IRRIGATION_MQTT_PORT ?= 1883
IRRIGATION_STALE_RETAINED_TOPICS := greenhouse/sensor/south_1_soil_moisture____/state greenhouse/sensor/south_1_soil_ec____s___cm_/state greenhouse/sensor/south_1_soil_temp____f_/state greenhouse/sensor/south_soil_moisture____/state greenhouse/sensor/south_soil_ec____s___cm_/state greenhouse/sensor/south_soil_temp____f_/state
IRRIGATION_STALE_NEAR_MISS_TOPICS := greenhouse/sensor/east_soil_moisture____/state greenhouse/sensor/south_2_soil_moisture____/state greenhouse/sensor/west_soil_moisture____/state
FIRMWARE_ESPHOME := scripts/firmware-esphome-worktree.sh
FIRMWARE_OTA_BIN := firmware/.esphome/build/greenhouse/.pioenvs/greenhouse/firmware.ota.bin
# #254: the firmware-deploy preflight DB handle is re-homed off the dead .150 VM
# (which ran `docker exec verdify-timescaledb`) to the k3s prod DB. The default
# backend is `kube` — lib/psql-verdify.sh runs `kubectl exec -n verdify-prod
# verdify-db-0 -c postgres -- psql ...` from any tooling host with a kubeconfig.
# Override to `docker` only if a local verdify-timescaledb container is reachable
# (legacy VM), or `dsn` when running in-cluster with PG*/POSTGRES_PASSWORD set.
FIRMWARE_DB_BACKEND ?= kube
# Authoritative for the current live ingestor pod. The mount remains temporary
# emptyDir under #382, so this pin is not durable across pod replacement yet.
FIRMWARE_STATE_DIR ?= /srv/verdify/state
FIRMWARE_EXPECTED_VERSION_FILE ?= $(FIRMWARE_STATE_DIR)/expected-firmware-version
FIRMWARE_STATE_NAMESPACE ?= verdify-prod
FIRMWARE_STATE_RESOURCE ?= deployment/verdify-ingestor
FIRMWARE_STATE_CONTAINER ?= ingestor
REPLAY_CORPUS_GZ := firmware/test/data/replay_overrides.csv.gz
REPLAY_CORPUS_TMP ?= /tmp/verdify-replay-overrides.csv
HERMES_IRIS_RUNTIME_DIR ?= /var/lib/verdify/hermes/iris
HERMES_IRIS_ENV_FILE ?= /etc/verdify/hermes-iris.env

.PHONY: help setup venv-check tool-check test test-fast test-live test-container lint format check lighting-audit-static lighting-audit-current lighting-audit-live lighting-audit-complete climate-intent-replay-report climate-authority-post-deploy-proof-plan climate-authority-post-deploy-proof firmware-check firmware-check-worktree firmware-check-all firmware-invariants firmware-replay firmware-replay-worktree firmware-replay-stream-check firmware-audit-traceability-proof firmware-audit-worktree-proof firmware-dwell-preview firmware-deploy firmware-archive-artifacts firmware-promote-last-good smoke hermes-deploy-config hermes-restart hermes-smoke clean migration-rollback-safety irrigation-migration-check irrigation-migration-proof irrigation-field-diagnostics irrigation-field-sensor-health-proof irrigation-stack-software-check irrigation-stack-check irrigation-feedback-check irrigation-feedback-discover irrigation-feedback-discovery-proof irrigation-feedback-work-order irrigation-feedback-work-order-proof irrigation-feedback-clear-stale-retained irrigation-feedback-clear-stale-near-misses irrigation-feedback-watch irrigation-feedback-watch-field irrigation-feedback-watch-field-proof irrigation-feedback-finalize-dry-run irrigation-feedback-finalize-dry-run-proof irrigation-feedback-finalize irrigation-feedback-finalize-proof irrigation-feedback-proof-json irrigation-sensor-health-proof irrigation-stack-proof irrigation-completion-audit irrigation-completion-audit-proof irrigation-acceptance irrigation-full-acceptance irrigation-post-deploy-acceptance-plan irrigation-post-deploy-acceptance

# These whole files are retired VM/live smoke suites. Mixed modules use the
# registered markers below so their static invariants remain in portable CI.
# The curated current-production suite is named explicitly by test-live; ignore
# it here as defense in depth against an ambient VERDIFY_TEST_LIVE=1.
PORTABLE_TEST_IGNORES := \
	--ignore=tests/test_01_infrastructure.py \
	--ignore=tests/test_03_api.py \
	--ignore=tests/test_06_website.py \
	--ignore=tests/test_09_api_responses.py \
	--ignore=tests/test_live_readonly.py
PORTABLE_TEST_MARKERS := not live_db and not live_http and not operator_probe and not legacy_host and not external_vault and not writable_db and not container_db

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

ci: ## Run the FULL pre-merge validation gate (lint+format+schema+logic tests, migrations, twin compile, overlay render); CI_BASE_REF=<ref> adds diff gates
	bash scripts/ci-local.sh

setup: ## Create/update the repo-local Python tooling venv
	BOOTSTRAP_PYTHON="$(BOOTSTRAP_PYTHON)" BOOTSTRAP_EXTRAS="$(BOOTSTRAP_EXTRAS)" VENV="$(VENV)" bash scripts/bootstrap-venv.sh

venv-check:
	@if [ ! -x "$(PYTHON)" ]; then \
		echo "Missing Python venv at $(VENV). Run: make setup"; \
		echo "Override with: make VENV=/path/to/venv <target>"; \
		exit 127; \
	fi

tool-check: venv-check
	@if [ ! -x "$(RUFF)" ]; then \
		echo "Missing ruff at $(RUFF). Run: make setup"; \
		exit 127; \
	fi

# ── Quality ─────────────────────────────────────────────────────────

lint: tool-check ## Run ruff linter on all Python files
	$(RUFF) check ingestor/ api/ mcp/ scripts/*.py tests/ verdify_schemas/

format: tool-check ## Auto-format Python files with ruff
	$(RUFF) format ingestor/ api/ mcp/ scripts/*.py tests/ verdify_schemas/
	$(RUFF) check --fix ingestor/ api/ mcp/ scripts/*.py tests/ verdify_schemas/

check: lint test lighting-audit-static test-firmware firmware-check ## Run all checks (lint + test + lighting audit + native firmware tests + firmware compile)
	@echo ""
	@echo "✓ All checks passed"

lighting-audit-static: ## Static lighting automation prompt-to-artifact audit
	$(PYTHON) scripts/audit-lighting-automation.py --static-only

lighting-audit-current: ## Live lighting audit; allow known OTA/post-OTA blocked status
	$(PYTHON) scripts/audit-lighting-automation.py --live --allow-blocked

lighting-audit-live: ## Strict live lighting audit; fails until OTA/post-OTA proof is complete
	$(PYTHON) scripts/audit-lighting-automation.py --live

lighting-audit-complete: ## Final lighting audit; requires OTA/post-OTA proof with no blockers
	$(PYTHON) scripts/audit-lighting-automation.py --live --require-ota

# ── Testing ─────────────────────────────────────────────────────────

test: venv-check ## Run the portable branch-validation suite (no live or writable dependencies)
	$(PYTEST) tests/ verdify_schemas/tests/ $(PORTABLE_TEST_IGNORES) -m '$(PORTABLE_TEST_MARKERS)'

test-fast: venv-check ## Run tests excluding slow planner tests
	$(PYTEST) tests/ verdify_schemas/tests/ $(PORTABLE_TEST_IGNORES) -m '$(PORTABLE_TEST_MARKERS)' -k "not Planner and not Context"

test-live: venv-check ## Run the explicit current-production read-only suite (no device/setpoints probe)
	VERDIFY_TEST_LIVE=1 VERDIFY_DB_BACKEND=kube $(PYTEST) tests/test_live_readonly.py

test-container: venv-check ## Run opt-in tests that create and remove disposable local containers
	$(PYTEST) tests/test_g5_dualwrite_validation.py -m container_db

climate-intent-replay-report: ## Replay ClimateIntent actions over the firmware corpus
	$(PYTHON) scripts/climate_intent_replay_evaluator.py --csv $(REPLAY_CORPUS_GZ)

climate-authority-post-deploy-proof-plan: ## Print post-merge/service-restart proof sequence before OTA
	@printf '%s\n' \
		'Climate authority post-deploy proof plan (prints only; does not restart services or run OTA):' \
		'1. Merge/deploy the reviewed branch to /srv/verdify.' \
		'2. Restart affected services: verdify-ingestor, verdify-api, verdify-setpoint-server, verdify-mcp.' \
		'3. Wait at least 120 seconds for ESPHome diagnostic republish and action-log writes.' \
		'4. Run make climate-authority-post-deploy-proof.' \
		'5. Only after that proof passes should an operator consider make firmware-deploy.'

climate-authority-post-deploy-proof: ## Prove ClimateIntent service path and OTA gates after service restart
	bash scripts/validate-climate-authority-post-deploy.sh

test-firmware: ## Run native C++ logic tests + replay against golden CSV (same code as ESP32)
	cd firmware && g++ -std=c++17 -I lib -o test/test_greenhouse test/test_greenhouse_logic.cpp && ./test/test_greenhouse
	# OBS-1e (Sprint 16) — replay validation against 8 months of real telemetry.
	# Required gate per CLAUDE.md Firmware Change Protocol: unit tests alone
	# cannot catch structural flag regressions (e.g. the shipped-and-caught
	# vpd_dry_override dead code in commit 82b18ad → patched in caa2cea).
	gzip -cd $(REPLAY_CORPUS_GZ) > $(REPLAY_CORPUS_TMP)
	cd firmware && g++ -std=c++17 -O2 -I lib -o test/replay_overrides test/replay_overrides.cpp
	set -o pipefail; ./firmware/test/replay_overrides $(REPLAY_CORPUS_TMP) | tail -30

test-replay-overrides: ## Validate evaluate_overrides() against full history + synthetic self-test (OBS-1e)
	bash scripts/export-replay-overrides.sh
	cd firmware && g++ -std=c++17 -O2 -I lib -o test/replay_overrides test/replay_overrides.cpp && ./test/replay_overrides test/data/replay_overrides.csv

firmware-replay-stream-check: ## TWIN-1/2: build the gated replay_emit_follow (--stream + climate_action) and smoke it; prove stock batch output byte-identical
	gzip -cd $(REPLAY_CORPUS_GZ) > $(REPLAY_CORPUS_TMP)
	cd firmware && g++ -std=c++17 -O2 -I lib -o test/replay_emit test/replay_emit.cpp
	cd firmware && g++ -std=c++17 -O2 -DREPLAY_EMIT_STREAM -I lib -o test/replay_emit_follow test/replay_emit.cpp
	# stock batch header is the unchanged 11-column schema (rule-8 gate intact)
	REPLAY_EMIT_FORCE_FSM=1 ./firmware/test/replay_emit $(REPLAY_CORPUS_TMP) 2>/dev/null | head -1 \
	    | grep -qx 'ts	mode	relay_fog	relay_vent	relay_fan1	relay_fan2	relay_heat1	relay_heat2	mist_stage	reason	override_bits' \
	    && echo "batch header byte-identical (11 cols) ✓"
	# stream build emits the additive climate_action column via describe_effective_climate_decision()
	REPLAY_EMIT_FORCE_FSM=1 sh -c 'tail -n +2 $(REPLAY_CORPUS_TMP) | head -50 | ./firmware/test/replay_emit_follow --stream --header-from $(REPLAY_CORPUS_TMP)' \
	    | head -1 | grep -q 'climate_action' \
	    && echo "stream header carries climate_action column ✓"
	REPLAY_EMIT_FORCE_FSM=1 sh -c 'tail -n +2 $(REPLAY_CORPUS_TMP) | head -50 | ./firmware/test/replay_emit_follow --stream --header-from $(REPLAY_CORPUS_TMP)' \
	    | tail -n +2 | awk -F"\t" 'NF==12{ok++} END{ if(ok>0){print "stream emitted "ok" decision rows w/ 12 cols ✓"} else {print "FAIL: no stream rows"; exit 1} }'

firmware-invariants: ## Phase-0: run 16 invariants from invariants.h against the replay corpus (pass = bulletproof gate green)
	gzip -cd $(REPLAY_CORPUS_GZ) > $(REPLAY_CORPUS_TMP)
	cd firmware && g++ -std=c++17 -O2 -I lib -o test/replay_invariants test/replay_invariants.cpp
	./firmware/test/replay_invariants $(REPLAY_CORPUS_TMP)

firmware-replay: ## Phase-0: dual-ref diff of firmware mode/relay decisions between OLD and NEW git refs
	@if [ -z "$(OLD)" ] || [ -z "$(NEW)" ]; then \
	    echo "Usage: make firmware-replay OLD=<ref> NEW=<ref>"; \
	    echo "       (e.g. OLD=HEAD~5 NEW=HEAD)"; \
	    exit 2; \
	fi
	bash scripts/firmware-replay-diff.sh "$(OLD)" "$(NEW)"

firmware-replay-worktree: ## Compare firmware behavior from OLD=<ref> against current uncommitted worktree
	bash scripts/firmware-replay-worktree-diff.sh "$(OLD)"

firmware-replay-band: ## BAND-CURVE behavioral diff: replay with setpoints DERIVED from band_value_at_phase (catches band-curve changes the stock corpus-fed replay MISSES). OLD=<ref> [NEW=<ref>]
	@# The stock replay feeds the band from the corpus sp_* columns, so a change
	@# to the band CURVE math shows ZERO divergence. This mode (REPLAY_EMIT_BAND_DERIVE=1)
	@# derives the band on-chip-style from band_value_at_phase at each row's solar
	@# phase, so curve changes produce a real mode/relay diff. Band changes are
	@# INTENTIONAL, so this is a REPORT (THRESHOLD_PCT defaults high) — review the %
	@# and the sample diff; it must not be 0-by-accident like the corpus replay.
	@if [ -z "$(OLD)" ]; then echo "Usage: make firmware-replay-band OLD=<ref> [NEW=<ref>]  (NEW omitted = current worktree)"; exit 2; fi
	@if [ -n "$(NEW)" ]; then \
	    REPLAY_EMIT_BAND_DERIVE=1 THRESHOLD_PCT=$${THRESHOLD_PCT:-100} bash scripts/firmware-replay-diff.sh "$(OLD)" "$(NEW)"; \
	else \
	    REPLAY_EMIT_BAND_DERIVE=1 THRESHOLD_PCT=$${THRESHOLD_PCT:-100} bash scripts/firmware-replay-worktree-diff.sh "$(OLD)"; \
	fi

firmware-audit-traceability-proof: ## Repeatable firmware audit proof across DB, registry, planner, docs, and generated site
	bash scripts/firmware-audit-traceability-proof.sh

firmware-audit-worktree-proof: ## Same audit proof but allow /srv source drift before merge/deploy
	FIRMWARE_AUDIT_ALLOW_LIVE_SOURCE_DRIFT=1 bash scripts/firmware-audit-traceability-proof.sh

firmware-dwell-preview: ## Phase-2: replay corpus with dwell-gate ON vs OFF, quantify whipsaw reduction
	cd firmware && g++ -std=c++17 -O2 -I lib -o test/replay_emit test/replay_emit.cpp
	bash scripts/firmware-dwell-preview.sh

replay-corpus-refresh: ## Refresh the replay corpus .csv.gz from live DB + validate no regression
	@bash -c '\
		set -euo pipefail; \
		CORPUS=firmware/test/data/replay_overrides.csv.gz; \
		PREV=firmware/test/data/replay_overrides.prev.csv.gz; \
		if [ -f "$$CORPUS" ]; then cp "$$CORPUS" "$$PREV"; echo "✓ snapshot existing → $$PREV"; fi; \
		OUTDIR=firmware/test/data bash scripts/export-replay-overrides.sh 0; \
		NEW=$$(wc -l < firmware/test/data/replay_overrides.csv); \
		OLD=0; [ -f "$$PREV" ] && OLD=$$(gunzip -c "$$PREV" | wc -l); \
		echo "  previous: $$OLD rows   new: $$NEW rows"; \
		if [ "$$OLD" -gt 0 ] && [ "$$NEW" -lt $$((OLD * 95 / 100)) ]; then \
			echo "✗ new corpus < 95%% of prior — aborting, restoring previous"; \
			cp "$$PREV" "$$CORPUS"; \
			rm -f firmware/test/data/replay_overrides.csv; \
			exit 1; \
		fi'
	@echo "─── Re-running replay gate against refreshed corpus ───"
	cd firmware && g++ -std=c++17 -O2 -I lib -o test/replay_overrides test/replay_overrides.cpp
	./firmware/test/replay_overrides firmware/test/data/replay_overrides.csv | tail -30
	@gzip -f firmware/test/data/replay_overrides.csv
	@echo "✓ refreshed corpus archived at firmware/test/data/replay_overrides.csv.gz"

test-v: ## Run tests with verbose output
	$(PYTEST) tests/ -v --tb=long

# ── Firmware ────────────────────────────────────────────────────────

firmware-check: ## Compile ESP32 firmware from this git worktree (validate only, no deploy)
	$(FIRMWARE_ESPHOME) compile

firmware-check-worktree: firmware-check ## Back-compat alias; firmware-check already uses this worktree

firmware-check-all: firmware-check ## Compile firmware from the only supported deploy source
	@echo "✓ Worktree firmware config compiles"

firmware-archive-artifacts: ## Archive ESPHome build outputs for FW_VERSION=<version>; set PROMOTE_LAST_GOOD=1 to update rollback target
	@if [ -z "$(FW_VERSION)" ]; then \
	    echo "Usage: make firmware-archive-artifacts FW_VERSION=<version> [PROMOTE_LAST_GOOD=1]"; \
	    exit 2; \
	fi
	@EXTRA=""; \
	if [ "$(PROMOTE_LAST_GOOD)" = "1" ]; then EXTRA="--promote-last-good"; fi; \
	bash scripts/archive-firmware-artifacts.sh "$(FW_VERSION)" $$EXTRA

firmware-promote-last-good: ## Promote a baked archived firmware FW_VERSION=<version> to rollback target
	@if [ -z "$(FW_VERSION)" ]; then \
	    echo "Usage: make firmware-promote-last-good FW_VERSION=<archived-version>"; \
	    exit 2; \
	fi
	@SRC="firmware/artifacts/$(FW_VERSION)"; \
	if [ ! -f "$$SRC/firmware.ota.bin" ] || [ ! -f "$$SRC/metadata.env" ]; then \
	    echo "Missing archived firmware artifacts under $$SRC"; \
	    exit 1; \
	fi; \
	cp "$$SRC/firmware.ota.bin" firmware/artifacts/last-good.ota.bin; \
	printf '%s\n' "$(FW_VERSION)" > firmware/artifacts/last-good.version; \
	cp "$$SRC/metadata.env" firmware/artifacts/last-good.metadata.env; \
	DEPLOYED_AT="$$(sed -n 's/^deployed_at=//p' "$$SRC/metadata.env" | tail -1)"; \
	if [ -n "$$DEPLOYED_AT" ]; then touch -d "$$DEPLOYED_AT" firmware/artifacts/last-good.ota.bin; fi; \
	echo "✓ Promoted rollback target: $(FW_VERSION)"

site-rebuild: ## Manually rebuild lab.verdify.ai site (watcher does this automatically on vault changes)
	bash scripts/rebuild-site.sh

site-publish-status: ## Trace Obsidian vault -> Quartz build -> nginx publish state
	bash scripts/site-publish-status.sh

site-doctor: ## Audit lab.verdify.ai source, build output, and Grafana embeds
	$(PYTHON) scripts/site-doctor.py
	$(PYTHON) scripts/brand-grafana-embeds.py --check
	$(PYTHON) scripts/brand-grafana-embeds.py --check --live

grafana-brand-check: ## Verify embedded Grafana panels use Verdify Lab styling rules
	$(PYTHON) scripts/brand-grafana-embeds.py --check

grafana-brand-check-live: ## Verify live embedded Grafana panels use Verdify Lab styling rules
	$(PYTHON) scripts/brand-grafana-embeds.py --check --live

grafana-cm-check: ## Verify generated dashboard ConfigMaps match grafana/dashboards JSON sources (#392)
	$(PYTHON) scripts/gen-grafana-dashboard-cms.py --check

solar-constants-check: ## Verify solar site constants (lat/lon/zenith) agree across ingestor/firmware/DB surfaces (#393)
	$(PYTHON) scripts/check-solar-constants.py

site-lint: ## Run cheap lint for public-site content and routes
	$(PYTHON) scripts/lint_public_site.py

migration-rollback-safety: ## Classify db/migrations as self-committing vs safe-to-wrap (#23 guard)
	@$(PYTHON) scripts/check_migration_rollback_safety.py --list

irrigation-migration-check: ## Replay irrigation migration 134 inside a rollback transaction
	@# Preflight (#23): refuse to wrap a self-committing migration in an outer
	@# BEGIN..ROLLBACK — its own top-level COMMIT / commit-forcing statement would
	@# defeat the rollback and commit to the LIVE DB (2026-05-30 live-commit incident).
	@$(PYTHON) scripts/check_migration_rollback_safety.py --rollback-wrap db/migrations/134-irrigation-fertigation-canonical.sql
	@set -o pipefail; . scripts/lib/psql-verdify.sh; { printf 'BEGIN;\n'; cat db/migrations/134-irrigation-fertigation-canonical.sql; printf '\nROLLBACK;\n'; } | verdify_psql_stdin -v ON_ERROR_STOP=1 -q
	@echo "OK: migration 134 replays cleanly in rollback transaction"

irrigation-migration-proof: ## Replay and persist irrigation migration rollback proof
	@mkdir -p "$(dir $(IRRIGATION_MIGRATION_PROOF))"
	@# Preflight (#23): refuse to wrap a self-committing migration (see irrigation-migration-check).
	@$(PYTHON) scripts/check_migration_rollback_safety.py --rollback-wrap db/migrations/134-irrigation-fertigation-canonical.sql
	@set -o pipefail; . scripts/lib/psql-verdify.sh; { { printf 'BEGIN;\n'; cat db/migrations/134-irrigation-fertigation-canonical.sql; printf '\nROLLBACK;\n'; } | verdify_psql_stdin -v ON_ERROR_STOP=1 -q && echo "OK: migration 134 replays cleanly in rollback transaction"; } 2>&1 | tee "$(IRRIGATION_MIGRATION_PROOF)"

irrigation-field-diagnostics: ## Run non-gating field diagnostics for physical feedback blockers
	$(MAKE) irrigation-field-sensor-health-proof
	$(MAKE) irrigation-feedback-work-order-proof
	$(MAKE) irrigation-completion-audit-proof
	$(MAKE) irrigation-feedback-discovery-proof
	$(MAKE) irrigation-feedback-finalize-dry-run-proof

irrigation-field-sensor-health-proof: ## Persist short-window sensor-health proof for field diagnostics
	@mkdir -p "$(dir $(IRRIGATION_FIELD_SENSOR_HEALTH_PROOF))"
	@set -o pipefail; $(MAKE) sensor-health SINCE='2 minutes' 2>&1 | tee "$(IRRIGATION_FIELD_SENSOR_HEALTH_PROOF)"

irrigation-stack-software-check: ## Audit irrigation software/dashboard requirements while hardware feedback is pending
	$(PYTHON) scripts/validate-irrigation-stack.py --software-only

irrigation-stack-check: ## Audit full irrigation requirements, including physical feedback gate
	$(MAKE) site-doctor
	$(PYTHON) scripts/validate-irrigation-stack.py --live-site

irrigation-feedback-check: ## Validate south probe + center root-zone/runoff feedback bring-up
	$(PYTHON) scripts/validate-irrigation-feedback.py --include-db-history

irrigation-feedback-discover: ## List HA/MQTT feedback-like sensor entities; tolerate known missing hardware
	@$(PYTHON) scripts/validate-irrigation-feedback.py --discover-ha --discover-mqtt --discover-mqtt-all --discover-esphome --include-db-history --mqtt-live-timeout-s $(IRRIGATION_MQTT_LIVE_TIMEOUT); rc=$$?; \
	if [ $$rc -eq 1 ]; then exit 0; fi; \
	exit $$rc

irrigation-feedback-discovery-proof: ## Persist HA/MQTT/ESPHome feedback discovery evidence for field diagnostics
	@mkdir -p "$(dir $(IRRIGATION_DISCOVERY_PROOF))"
	@set -o pipefail; { $(PYTHON) scripts/validate-irrigation-feedback.py --discover-ha --discover-mqtt --discover-mqtt-all --discover-esphome --include-db-history --mqtt-live-timeout-s $(IRRIGATION_MQTT_LIVE_TIMEOUT); rc=$$?; if [ $$rc -eq 1 ]; then exit 0; fi; exit $$rc; } 2>&1 | tee "$(IRRIGATION_DISCOVERY_PROOF)"

irrigation-feedback-work-order: irrigation-feedback-work-order-proof ## Print and persist field checklist for remaining irrigation feedback work

irrigation-feedback-work-order-proof: ## Print and persist field checklist for remaining irrigation feedback work
	@mkdir -p "$(dir $(IRRIGATION_WORK_ORDER_PROOF))"
	@set -o pipefail; $(PYTHON) scripts/validate-irrigation-feedback.py --work-order --mqtt-live-timeout-s $(IRRIGATION_FIELD_WATCH_MQTT_TIMEOUT) 2>&1 | tee "$(IRRIGATION_WORK_ORDER_PROOF)"

irrigation-feedback-clear-stale-retained: ## Clear known stale retained MQTT feedback values after discovery confirms no live MQTT updates
	@if [ "$(CONFIRM_CLEAR_RETAINED)" != "1" ]; then \
		echo "Refusing to clear retained MQTT values without CONFIRM_CLEAR_RETAINED=1"; \
		echo "First run: make irrigation-feedback-discover IRRIGATION_MQTT_LIVE_TIMEOUT=75"; \
		exit 2; \
	fi
	$(PYTHON) scripts/clear-irrigation-stale-retained.py --confirm

irrigation-feedback-clear-stale-near-misses: ## Clear known retained near-match soil topics that are not accepted feedback inputs
	@if [ "$(CONFIRM_CLEAR_RETAINED)" != "1" ]; then \
		echo "Refusing to clear retained MQTT near-miss values without CONFIRM_CLEAR_RETAINED=1"; \
		echo "First run: make irrigation-feedback-discover IRRIGATION_MQTT_LIVE_TIMEOUT=75"; \
		exit 2; \
	fi
	$(PYTHON) scripts/clear-irrigation-stale-retained.py --confirm --near-miss

irrigation-feedback-watch: ## Poll until physical feedback rows are healthy; alert resolution is finalized separately
	$(PYTHON) scripts/validate-irrigation-feedback.py --watch --status-only --timeout-s $(IRRIGATION_FEEDBACK_TIMEOUT) --interval-s $(IRRIGATION_FEEDBACK_INTERVAL)

irrigation-feedback-watch-field: irrigation-feedback-watch-field-proof ## Field watch with DB, HA, MQTT, and ESPHome feedback evidence during repair/install

irrigation-feedback-watch-field-proof: ## Persist field watch evidence during repair/install
	@mkdir -p "$(dir $(IRRIGATION_FIELD_WATCH_PROOF))"
	@set -o pipefail; $(PYTHON) scripts/validate-irrigation-feedback.py --watch --status-only --discover-ha --discover-mqtt --discover-mqtt-all --discover-esphome --include-db-history --mqtt-live-timeout-s $(IRRIGATION_FIELD_WATCH_MQTT_TIMEOUT) --timeout-s $(IRRIGATION_FEEDBACK_TIMEOUT) --interval-s $(IRRIGATION_FEEDBACK_INTERVAL) 2>&1 | tee "$(IRRIGATION_FIELD_WATCH_PROOF)"

irrigation-feedback-finalize-dry-run: ## Check planned irrigation feedback closure without mutating DB rows
	$(PYTHON) scripts/finalize-irrigation-feedback.py --dry-run

irrigation-feedback-finalize-dry-run-proof: ## Persist non-mutating finalizer dry-run proof; tolerate known physical blockers
	@mkdir -p "$(dir $(IRRIGATION_FINALIZER_DRY_RUN_PROOF))"
	@set -o pipefail; $(PYTHON) scripts/finalize-irrigation-feedback.py --dry-run 2>&1 | tee "$(IRRIGATION_FINALIZER_DRY_RUN_PROOF)"; rc=$${PIPESTATUS[0]}; if [ $$rc -eq 1 ] && grep -q '^Irrigation feedback still blocked: .*not_ok=' "$(IRRIGATION_FINALIZER_DRY_RUN_PROOF)"; then exit 0; fi; exit $$rc

irrigation-feedback-finalize: irrigation-feedback-finalize-proof ## Resolve irrigation feedback alerts after physical feedback validates

irrigation-feedback-finalize-proof: ## Resolve irrigation feedback alerts and persist finalizer closure proof
	@mkdir -p "$(dir $(IRRIGATION_FINALIZER_PROOF))"
	@set -o pipefail; { $(PYTHON) scripts/validate-irrigation-feedback.py --status-only --discover-ha --discover-mqtt --discover-mqtt-all --discover-esphome --include-db-history --mqtt-live-timeout-s $(IRRIGATION_FIELD_WATCH_MQTT_TIMEOUT) && $(PYTHON) scripts/finalize-irrigation-feedback.py --dry-run && $(PYTHON) scripts/finalize-irrigation-feedback.py && $(PYTHON) scripts/validate-irrigation-feedback.py --include-db-history; } 2>&1 | tee "$(IRRIGATION_FINALIZER_PROOF)"

irrigation-feedback-proof-json: ## Emit machine-readable final irrigation feedback proof
	@mkdir -p "$(dir $(IRRIGATION_FEEDBACK_PROOF))"
	@set -o pipefail; $(PYTHON) scripts/validate-irrigation-feedback.py --json --discover-ha --discover-mqtt --discover-mqtt-all --discover-esphome --include-db-history --mqtt-live-timeout-s $(IRRIGATION_FIELD_WATCH_MQTT_TIMEOUT) | tee "$(IRRIGATION_FEEDBACK_PROOF)"

irrigation-sensor-health-proof: ## Run and persist final sensor-health proof for irrigation acceptance
	@mkdir -p "$(dir $(IRRIGATION_SENSOR_HEALTH_PROOF))"
	@set -o pipefail; $(MAKE) sensor-health SINCE='5 minutes' 2>&1 | tee "$(IRRIGATION_SENSOR_HEALTH_PROOF)"

irrigation-stack-proof: ## Run and persist strict live irrigation stack proof
	@mkdir -p "$(dir $(IRRIGATION_STACK_PROOF))"
	@set -o pipefail; { $(MAKE) site-doctor && $(PYTHON) scripts/validate-irrigation-stack.py --live-site; } 2>&1 | tee "$(IRRIGATION_STACK_PROOF)"

irrigation-completion-audit: ## Strict objective-level audit for final irrigation completion
	$(PYTHON) scripts/irrigation-completion-audit.py --live-site

irrigation-completion-audit-proof: ## Persist current objective-level audit; tolerate known physical blockers
	@mkdir -p "$(dir $(IRRIGATION_COMPLETION_AUDIT_PROOF))"
	@set -o pipefail; $(PYTHON) scripts/irrigation-completion-audit.py --json --live-site --allow-physical-blocker --mqtt-live-timeout-s $(IRRIGATION_FIELD_WATCH_MQTT_TIMEOUT) | tee "$(IRRIGATION_COMPLETION_AUDIT_PROOF)"

irrigation-acceptance: ## Wait for physical feedback, resolve alerts, then run strict irrigation audit
	$(MAKE) irrigation-feedback-watch-field-proof
	$(MAKE) irrigation-feedback-discovery-proof
	$(MAKE) irrigation-sensor-health-proof
	$(MAKE) irrigation-feedback-finalize
	$(MAKE) irrigation-feedback-proof-json
	$(MAKE) irrigation-stack-proof
	$(MAKE) irrigation-completion-audit-proof
	$(MAKE) irrigation-completion-audit

irrigation-full-acceptance: ## Full final proof: lint, tests, migration replay, and strict live irrigation audit
	$(MAKE) lint
	$(MAKE) test
	$(MAKE) irrigation-migration-proof
	$(MAKE) irrigation-acceptance

irrigation-post-deploy-acceptance-plan: ## Print non-mutating post-deploy acceptance sequence
	@printf '%s\n' \
		'Post-deploy irrigation acceptance plan (prints only; does not run checks):' \
		'1. make lint' \
		'2. make test' \
		'3. make irrigation-migration-proof' \
		'4. make irrigation-feedback-watch-field-proof' \
		'5. make irrigation-feedback-discovery-proof' \
		'6. make irrigation-sensor-health-proof' \
		'7. make irrigation-feedback-finalize' \
		'8. make irrigation-feedback-proof-json' \
		'9. make irrigation-stack-proof' \
		'10. make irrigation-completion-audit-proof' \
		'11. make irrigation-completion-audit' \
		'Run make irrigation-post-deploy-acceptance only after reviewed merge, service restart, and live site/dashboard publication.'

irrigation-post-deploy-acceptance: irrigation-full-acceptance ## Post-deploy production proof after merge/restart/site publish

# Safety: spell nested calls below as plain `make`, not the special $(MAKE)
# variable. GNU Make executes any recipe line containing $(MAKE) even under
# `make -n`; this target's acceptance chain must remain a true no-op in dry-run.
firmware-deploy: ## Compile + OTA deploy to ESP32 + post-deploy sensor-health sweep + auto-rollback on failure
	VERDIFY_DB_BACKEND=$(FIRMWARE_DB_BACKEND) bash scripts/firmware-deploy-preflight.sh
	@kubectl -n '$(FIRMWARE_STATE_NAMESPACE)' exec '$(FIRMWARE_STATE_RESOURCE)' -c '$(FIRMWARE_STATE_CONTAINER)' -- \
		sh -ceu 'test -d "$$1" && test -w "$$1"' -- '$(FIRMWARE_STATE_DIR)' || { \
		echo "✗ Live ingestor state dir is not reachable and writable: $(FIRMWARE_STATE_DIR)"; \
		echo "  Refusing OTA because the authoritative expected-version pin could not be updated."; \
		exit 1; \
	}
	@mkdir -p firmware/artifacts
	@DIRTY="$$(git diff --quiet -- . && git diff --cached --quiet -- . || echo .dirty)"; \
	if [ -n "$$DIRTY" ] && [ "$(ALLOW_DIRTY_FIRMWARE_DEPLOY)" != "1" ]; then \
		echo "✗ Dirty firmware OTA refused. Commit/stash changes or rerun with ALLOW_DIRTY_FIRMWARE_DEPLOY=1 for an operator-approved emergency."; \
		git status --short; \
		exit 1; \
	elif [ -n "$$DIRTY" ] && { [ "$(FIRMWARE_DEPLOY_OPERATOR_SIGNOFF)" != "1" ] || [ -z "$(FIRMWARE_DEPLOY_OVERRIDE_REASON)" ]; }; then \
		echo "✗ Dirty firmware OTA override requires FIRMWARE_DEPLOY_OPERATOR_SIGNOFF=1 and FIRMWARE_DEPLOY_OVERRIDE_REASON."; \
		exit 1; \
	fi; \
	FW_VERSION="$$(date +%Y.%-m.%-d.%H%M).$$(git rev-parse --short HEAD)$$DIRTY"; \
	echo "$$FW_VERSION" > firmware/artifacts/pending-fw-version.txt; \
	echo "─── Deploying fw_version=$$FW_VERSION ───"; \
	: "#301 — auto-source the OTA password from k3s when unset for this compile/upload"; \
	: "shell. The recursive rollback target resolves it independently because Make"; \
	: "recipes run in separate shells. Resolution runs only here,"; \
	: "no parse-time kubectl. The ESPHome upload still reads ota_password from the"; \
	: "reconstructed secrets.yaml (SECRETS_SRC); see docs/runbooks/laptop-operator.md."; \
	: "$${OTA_PW:=$$(kubectl -n verdify-prod get secret verdify-firmware-ota -o jsonpath='{.data.ota_password}' 2>/dev/null | base64 -d)}"; \
	test -n "$$OTA_PW" || { \
		echo "✗ Rollback OTA password unavailable from env or verdify-firmware-ota; refusing upload." ; \
		exit 1 ; \
	} ; \
	export OTA_PW; \
	$(FIRMWARE_ESPHOME) -s fw_version "$$FW_VERSION" compile && \
	$(FIRMWARE_ESPHOME) -s fw_version "$$FW_VERSION" upload --device "$(ESP32_DEVICE)"
	@echo ""
	@echo "Waiting 60s for ESP32 reboot + ingestor reconnect + first diagnostics cycle..."
	@sleep 60
	# FW-15 (Sprint 17): sensor-health decides whether this deploy is accepted.
	# Pass → archive the new binary and update the expected-firmware pin.
	# Rollback target stays on the prior last-good until an explicit
	# firmware-promote-last-good after the 48-hour bake.
	# Fail → flash last-good back to ESP32 via firmware-rollback.sh.
	@if VERDIFY_DB_BACKEND=$(FIRMWARE_DB_BACKEND) bash scripts/wait-for-firmware-version.sh "$$(cat firmware/artifacts/pending-fw-version.txt)" --timeout 180 && \
		EXPECTED_FW_VERSION="$$(cat firmware/artifacts/pending-fw-version.txt)" make sensor-health SINCE='5 minutes' && \
		FIRMWARE_DEPLOYED_AT="$$(date '+%Y-%m-%dT%H:%M:%S%z')" bash scripts/archive-firmware-artifacts.sh "$$(cat firmware/artifacts/pending-fw-version.txt)" && \
		kubectl -n '$(FIRMWARE_STATE_NAMESPACE)' exec -i '$(FIRMWARE_STATE_RESOURCE)' -c '$(FIRMWARE_STATE_CONTAINER)' -- \
			sh -ceu 'target="$$1"; tmp="$$target.tmp"; cat > "$$tmp"; chmod 0644 "$$tmp"; mv "$$tmp" "$$target"' \
			-- '$(FIRMWARE_EXPECTED_VERSION_FILE)' < firmware/artifacts/pending-fw-version.txt; then \
		echo "✓ Deploy accepted. Archived build outputs + promoted expected firmware pin. Rollback target unchanged while this build bakes." ; \
	else \
		echo "" ; \
		echo "▓▓▓  SENSOR-HEALTH FAILED POST-OTA  —  initiating auto-rollback  ▓▓▓" ; \
		ROLLBACK_VERSION="$$(cat firmware/artifacts/last-good.version 2>/dev/null)" ; \
		make firmware-rollback || { \
			echo "✗ Rollback flash failed; rejected firmware may still be running." ; \
			exit 1 ; \
		} ; \
		echo "" ; \
		echo "Waiting 60s for ESP32 to reboot onto rolled-back firmware..." ; \
		sleep 60 ; \
		if [ -n "$$ROLLBACK_VERSION" ]; then \
			echo "Verifying rolled-back firmware version and sensor health:" ; \
			VERDIFY_DB_BACKEND=$(FIRMWARE_DB_BACKEND) bash scripts/wait-for-firmware-version.sh "$$ROLLBACK_VERSION" --timeout 180 && \
			EXPECTED_FW_VERSION="$$ROLLBACK_VERSION" make sensor-health SINCE='5 minutes' || { \
				echo "✗ Rollback flash completed but recovery verification failed." ; \
				exit 1 ; \
			} ; \
		else \
			echo "⚠ Rollback flashed, but last-good.version is missing; exact-version verification is unavailable." ; \
			make sensor-health SINCE='5 minutes' || { \
				echo "✗ Rollback flash completed but sensor-health recovery verification failed." ; \
				exit 1 ; \
			} ; \
		fi ; \
		exit 1 ; \
	fi

firmware-rollback: ## Manually flash the saved last-good.ota.bin back onto the ESP32
	@OTA_PW="$${OTA_PW:-$$(kubectl -n verdify-prod get secret verdify-firmware-ota -o jsonpath='{.data.ota_password}' 2>/dev/null | base64 -d)}"; \
		test -n "$$OTA_PW" || { echo "✗ OTA password unavailable from env or verdify-firmware-ota"; exit 1; }; \
		export OTA_PW; \
		FIRMWARE_ROLLBACK_LOG="$${FIRMWARE_ROLLBACK_LOG:-firmware/artifacts/firmware-rollback.log}" \
		bash scripts/firmware-rollback.sh firmware/artifacts/last-good.ota.bin

sensor-health: ## Run sensor health sweep (layer 3 of Firmware Change Protocol)
	VERDIFY_DB_BACKEND=$(FIRMWARE_DB_BACKEND) SINCE='$(or $(SINCE),5 minutes)' EXPECTED_FW_VERSION='$(EXPECTED_FW_VERSION)' bash scripts/sensor-health-sweep.sh

greenhouse-quiet-on: ## Temporarily suppress routine greenhouse automations for recording (QUIET_MINUTES=30)
	$(PYTHON) scripts/greenhouse-quiet-mode.py enable --minutes $(QUIET_MINUTES)

greenhouse-quiet-off: ## Restore greenhouse quiet-mode setpoints now
	$(PYTHON) scripts/greenhouse-quiet-mode.py disable

greenhouse-quiet-status: ## Show recording quiet-mode status
	$(PYTHON) scripts/greenhouse-quiet-mode.py status

# ── Planner (event-driven via Iris agent) ────────────────────────────

planner-publish: ## Publish today's plan to lab.verdify.ai
	bash scripts/publish-daily-plan.sh

planner-dry: ## Dry-run planner prompts — render every event type and assert G2/G4/G7 invariants
	@$(PYTHON) scripts/planner-dry.py

# ── Hermes ─────────────────────────────────────────────────────────

hermes-deploy-config: venv-check ## Validate the GitOps-managed Hermes config (live delivery requires the gated prod sync)
	@$(PYTEST) tests/test_17_planner_health_surface.py -k hermes_profile_pins_gpt_5_6_sol_xhigh_at_the_runtime_key
	@kubectl kustomize deploy/k8s/overlays/prod >/dev/null
	@echo "✓ Canonical Hermes profile matches its k3s ConfigMap mirror and the prod overlay renders; no live mutation performed."
	@echo "  Merge the reviewed desired state, use the gated verdify-prod-dark sync (the profile checksum rolls and reseeds Hermes), then run make hermes-smoke."

hermes-restart: ## Restart the k3s Hermes Deployment after its GitOps config is synced (CONFIRM_PROD_RESTART=1)
	@if [ "$(CONFIRM_PROD_RESTART)" != "1" ]; then \
		echo "Refusing prod Hermes restart without CONFIRM_PROD_RESTART=1"; \
		exit 2; \
	fi
	kubectl -n verdify-prod rollout restart deployment/verdify-hermes-iris
	kubectl -n verdify-prod rollout status deployment/verdify-hermes-iris --timeout=180s

hermes-smoke: ## Wait for the desired Hermes ConfigMap/profile checksum, rollout, and availability
	@EXPECTED="$$(awk '/verdify.ai\/hermes-profile-sha256:/ {print $$2; exit}' deploy/k8s/components/hermes-iris/hermes-iris.yaml)"; \
		test -n "$$EXPECTED" || { echo "Missing desired Hermes profile checksum"; exit 1; }; \
		if command -v sha256sum >/dev/null 2>&1; then \
			HASHER=sha256sum; \
		elif command -v shasum >/dev/null 2>&1; then \
			HASHER="shasum -a 256"; \
		else \
			echo "Hermes smoke requires sha256sum or shasum"; \
			exit 1; \
		fi; \
		DEADLINE=$$((SECONDS + 180)); \
		while :; do \
			if ! LIVE_CONFIG_HASH="$$(set -o pipefail; \
				kubectl -n verdify-prod get configmap/verdify-hermes-iris-config \
					-o go-template='{{index .data "config.yaml"}}' \
					| $$HASHER | awk '{print $$1}')"; then \
				echo "Could not read and hash the live Hermes ConfigMap"; \
				exit 1; \
			fi; \
			[ "$$LIVE_CONFIG_HASH" = "$$EXPECTED" ] && break; \
			(( SECONDS < DEADLINE )) || { \
				echo "Live Hermes ConfigMap did not converge to the reviewed profile within 180s"; \
				exit 1; \
			}; \
			sleep 2; \
		done; \
		kubectl -n verdify-prod wait \
			--for="jsonpath={.spec.template.metadata.annotations.verdify\\.ai/hermes-profile-sha256}=$$EXPECTED" \
			deployment/verdify-hermes-iris --timeout=180s
	kubectl -n verdify-prod rollout status deployment/verdify-hermes-iris --timeout=180s
	kubectl -n verdify-prod wait --for=condition=Available deployment/verdify-hermes-iris --timeout=120s

# ── Stack ───────────────────────────────────────────────────────────
# The VM-era `docker compose` lifecycle targets (up/down/ps/logs) were removed
# with the docker-compose.yml stack on the k3s single-env migration. Use
# `kubectl -n verdify-prod ...` / ArgoCD for the live stack.

ingestor-restart: ## Restart the sole-writer k3s Deployment (CONFIRM_PROD_RESTART=1; device gate applies)
	@if [ "$(CONFIRM_PROD_RESTART)" != "1" ]; then \
		echo "Refusing prod ingestor restart without CONFIRM_PROD_RESTART=1"; \
		exit 2; \
	fi
	kubectl -n verdify-prod rollout restart deployment/verdify-ingestor
	kubectl -n verdify-prod rollout status deployment/verdify-ingestor --timeout=180s

ingestor-logs: ## Tail the sole-writer k3s Deployment logs
	kubectl -n verdify-prod logs deployment/verdify-ingestor --all-containers=true --tail=200 -f

# ── Database ────────────────────────────────────────────────────────

db-shell: ## Open psql shell (k3s prod verdify-db)
	scripts/verdify-db.sh prod

db-dump: ## Dump schema to db/schema.sql (k3s prod verdify-db, read-only)
	kubectl exec -n verdify-prod verdify-db-0 -c postgres -- pg_dump -U verdify -d verdify --schema-only > db/schema.sql

db-scorecard: ## Show today's planner scorecard
	@. scripts/lib/psql-verdify.sh; verdify_psql -c "SELECT * FROM fn_planner_scorecard(CURRENT_DATE);"

# ── Cleanup ─────────────────────────────────────────────────────────

clean: ## Remove Python bytecode and pytest cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

planning-validate: ## Validate planning/backlog.yaml against the pydantic schema (lane/wave plan consistency)
	$(PYTHON) planning/schema.py planning/backlog.yaml
	$(PYTHON) -m pytest planning/tests/test_backlog.py -q
