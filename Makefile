.PHONY: up down build test test-api test-cli test-ui test-vscode build-vscode test-jetbrains build-jetbrains verify-jetbrains test-integration lint scan onboard seed-db logs ps metrics

up:
	docker compose up -d --wait

down:
	docker compose down

build:
	docker compose build

test: test-api test-cli test-vscode test-jetbrains

test-api:
	cd services/api && python -m pytest tests/ -v

test-cli:
	cd services/cli && python -m pytest tests/ -q

test-ui:
	cd services/ui && npm run test:e2e

test-vscode:
	cd services/vscode && npm run typecheck && npm test

build-vscode:
	cd services/vscode && npm run package

# JetBrains plugin. Needs a JDK ≥ 17 to run Gradle (a JDK 21 toolchain is auto-provisioned). With no
# JAVA_HOME and no java on PATH, fall back to a Toolbox-installed JetBrains Runtime.
JB_JAVA_HOME ?= $(or $(JAVA_HOME),$(shell command -v java >/dev/null 2>&1 || ls -d $$HOME/.local/share/JetBrains/Toolbox/apps/*/jbr 2>/dev/null | head -1))
test-jetbrains:
	cd services/jetbrains && JAVA_HOME=$(JB_JAVA_HOME) ./gradlew --console=plain test
build-jetbrains:
	cd services/jetbrains && JAVA_HOME=$(JB_JAVA_HOME) ./gradlew --console=plain buildPlugin
# Downloads the IDEs listed in build.gradle.kts's pluginVerification block on first run (cached
# after that) and checks the built plugin against each for compatibility problems. Slow; be patient.
verify-jetbrains:
	cd services/jetbrains && JAVA_HOME=$(JB_JAVA_HOME) ./gradlew --console=plain verifyPlugin

test-integration:
	cd services/api && python -m pytest tests/test_integration_e2e.py -v -m integration

lint:
	pre-commit run --all-files

scan:
	trivy config . && checkov -d .

onboard:
	bash scripts/onboard.sh

seed-db:
	bash scripts/seed-dev-users.sh

logs:
	docker compose logs -f

ps:
	docker compose ps

metrics:
	curl -s http://localhost:8001/metrics
