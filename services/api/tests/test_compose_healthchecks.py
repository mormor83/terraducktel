"""
Test that required services have healthcheck blocks in docker-compose.yml.
This is a structural validation test — RED before adding healthchecks.
"""
import yaml
import os


COMPOSE_PATH = os.path.join(
    os.path.dirname(__file__), "../../../docker-compose.yml"
)

SERVICES_REQUIRING_HEALTHCHECK = ["act_runner", "traefik", "drift-detector"]


def load_compose():
    with open(COMPOSE_PATH) as f:
        return yaml.safe_load(f)


def test_act_runner_has_healthcheck():
    compose = load_compose()
    service = compose["services"]["act_runner"]
    assert "healthcheck" in service, "act_runner must have a healthcheck block"
    hc = service["healthcheck"]
    assert "test" in hc
    assert "interval" in hc
    assert "timeout" in hc
    assert "retries" in hc
    assert "start_period" in hc


def test_traefik_has_healthcheck():
    compose = load_compose()
    service = compose["services"]["traefik"]
    assert "healthcheck" in service, "traefik must have a healthcheck block"
    hc = service["healthcheck"]
    assert "test" in hc
    assert "interval" in hc
    assert "timeout" in hc
    assert "retries" in hc
    assert "start_period" in hc


def test_traefik_has_ping_flag():
    compose = load_compose()
    service = compose["services"]["traefik"]
    commands = service.get("command", [])
    assert "--ping" in commands, "traefik command must include --ping flag"


def test_drift_detector_has_healthcheck():
    compose = load_compose()
    service = compose["services"]["drift-detector"]
    assert "healthcheck" in service, "drift-detector must have a healthcheck block"
    hc = service["healthcheck"]
    assert "test" in hc
    assert "interval" in hc
    assert "timeout" in hc
    assert "retries" in hc
    assert "start_period" in hc
