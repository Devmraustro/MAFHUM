"""Static contract for the self-hosted production Compose stack.

`docker compose config` proves that a YAML file *parses*. It does not prove that
the stack fails closed. This checker asserts the security invariants that a
parse-only validation cannot see: a real ClamAV service exists and is healthy
before the API starts, scanning is mandatory in production, secrets are
interpolated rather than baked in, and no service is published except Caddy.

It deliberately reads the files as data with PyYAML rather than shelling out to
`docker compose config`, so it can run in the ordinary pytest suite with no
Docker daemon. The Compose *config* command is run separately in CI on a runner
that does have Docker.

Usage:
    python tools/acceptance/prod_compose_contract.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"
COMPOSE = DEPLOY / "docker-compose.prod.yml"
ENV_EXAMPLE = DEPLOY / ".env.prod.example"
CADDYFILE = DEPLOY / "Caddyfile"
CLAMD_DIR = DEPLOY / "clamd"

# Placeholders, never real credentials. A value that does not look like one of
# these in the template is suspicious and must be reviewed by a human.
_PLACEHOLDER = re.compile(
    r"^(REPLACE_WITH_|change-me|operator@change-me|mafhum$|0$|true$|false$|clamd$|3310$|10\.0$)"
)


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not a YAML mapping")
    return data


def _env_template() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def check_stack() -> list[str]:
    """Return a list of human-readable contract violations (empty == pass)."""
    failures: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    for path in (COMPOSE, ENV_EXAMPLE, CADDYFILE, CLAMD_DIR / "Dockerfile"):
        require(path.is_file(), f"missing required file: {path.name}")

    if failures:
        return failures

    compose = _load_yaml(COMPOSE)
    services: dict[str, Any] = compose.get("services") or {}
    volumes: dict[str, Any] = compose.get("volumes") or {}
    env = _env_template()

    # --- real ClamAV service ------------------------------------------------
    clamd = services.get("clamd")
    require(isinstance(clamd, dict), "no `clamd` service: production would scan nothing")
    if isinstance(clamd, dict):
        require("build" in clamd, "clamd must be built from deploy/clamd, not pulled ad hoc")
        require(
            "3310" in [str(p) for p in clamd.get("expose", [])],
            "clamd must expose TCP 3310 on the internal network",
        )
        require(
            "ports" not in clamd,
            "clamd must NOT publish a host port (only the API may reach it)",
        )
        health = clamd.get("healthcheck") or {}
        require(
            "healthcheck.sh" in str(health.get("test", "")),
            "clamd needs a healthcheck that performs a real SCAN, not just a connect",
        )
        # `start_period` may be a string ("900s") or an int of nanoseconds.
        require("start_period" in health, "clamd healthcheck needs a long start_period")
        require(
            any("/var/lib/clamav" in str(v) for v in clamd.get("volumes", [])),
            "clamd signature database must live on a named volume",
        )
        require(
            clamd.get("security_opt") and "no-new-privileges:true" in clamd["security_opt"],
            "clamd must set no-new-privileges",
        )

    # --- API is gated on a healthy engine ------------------------------------
    api = services.get("api") or {}
    api_env: dict[str, Any] = api.get("environment") or {}
    depends = api.get("depends_on") or {}
    require(
        isinstance(depends, dict)
        and (depends.get("clamd") or {}).get("condition") == "service_healthy",
        "api must depend on clamd: condition=service_healthy",
    )
    require(
        str(api_env.get("MALWARE_SCAN_REQUIRED", "")).lower() == "true",
        "production MUST hard-code MALWARE_SCAN_REQUIRED=true",
    )
    require(
        api_env.get("CLAMD_HOST") == "clamd",
        "api must point CLAMD_HOST at the clamd service",
    )
    require(api_env.get("APP_ENV") == "production", "api must run with APP_ENV=production")
    require(api.get("read_only") is True, "api must use a read-only root filesystem")
    require(
        any("tmp" in str(t) for t in api.get("tmpfs", [])),
        "api needs a writable tmpfs for bounded spooling",
    )

    # --- secrets come from required interpolation ----------------------------
    for name in ("SESSION_SECRET", "METRICS_SCRAPE_TOKEN", "ALLOWED_ORIGINS"):
        value = str(api_env.get(name, ""))
        require(
            value.startswith("${") and ":?" in value,
            f"api.{name} must be a REQUIRED env interpolation, not a literal",
        )
    db_url = str(api_env.get("DATABASE_URL", ""))
    require(":?" in db_url, "DATABASE_URL credentials must come from required env vars")
    require(
        "REPLACE_WITH" not in db_url and "change_me" not in db_url,
        "DATABASE_URL contains a template placeholder",
    )

    # --- no floating image tags, no unexpected host ports --------------------
    for name, service in services.items():
        image = str(service.get("image", ""))
        if image:
            require(
                ":" in image.rsplit("/", 1)[-1] and not image.endswith(":latest"),
                f"service {name}: image must be pinned to a tag other than :latest",
            )
        require(bool(service.get("restart")), f"service {name} needs a restart policy")
        if name != "caddy":
            require("ports" not in service, f"service {name} must not publish host ports")
    require("ports" in (services.get("caddy") or {}), "caddy must be the public entry point")

    # --- every named volume is declared --------------------------------------
    referenced: set[str] = set()
    for service in services.values():
        for mount in service.get("volumes", []) or []:
            if isinstance(mount, str) and ":" in mount:
                source = mount.split(":", 1)[0]
                if not source.startswith((".", "/")):
                    referenced.add(source)
    require(
        referenced <= set(volumes),
        f"undeclared named volumes: {sorted(referenced - set(volumes))}",
    )
    require("clamd_db" in volumes, "clamd_db named volume must be declared")

    # --- env template ---------------------------------------------------------
    required_env = {
        "MAFHUM_DOMAIN",
        "MAFHUM_ACME_EMAIL",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "SESSION_SECRET",
        "ALLOWED_ORIGINS",
        "MALWARE_SCAN_REQUIRED",
        "CLAMD_HOST",
        "CLAMD_PORT",
        "METRICS_SCRAPE_TOKEN",
        "AUDIT_RETENTION_DAYS",
    }
    require(
        required_env <= set(env),
        f".env.prod.example is missing: {sorted(required_env - set(env))}",
    )
    require(
        env.get("MALWARE_SCAN_REQUIRED", "").lower() == "true",
        ".env.prod.example must set MALWARE_SCAN_REQUIRED=true",
    )
    for key, value in env.items():
        if key in {"SESSION_SECRET", "METRICS_SCRAPE_TOKEN", "POSTGRES_PASSWORD"}:
            require(
                value.startswith("REPLACE_WITH_"),
                f".env.prod.example:{key} is not a placeholder",
            )
        require(
            not re.search(
                r"(sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|BEGIN .*PRIVATE KEY)", value
            ),
            f".env.prod.example:{key} looks like a real secret",
        )
        require(
            _PLACEHOLDER.match(value) is not None
            or key
            in {
                "ALLOWED_ORIGINS",
                "CLAMD_HOST",
                "POSTGRES_USER",
                "MAFHUM_DOMAIN",
                "MAFHUM_ACME_EMAIL",
                "POSTGRES_DB",
            },
            f".env.prod.example:{key} has an unexpected non-placeholder value: {value!r}",
        )

    # --- ClamAV configuration files ------------------------------------------
    clamd_conf_lines = [
        line.strip()
        for line in (CLAMD_DIR / "clamd.conf").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    clamd_conf = "\n".join(clamd_conf_lines)
    require("TCPSocket 3310" in clamd_conf, "clamd.conf must listen on TCP 3310")
    require("LocalSocket" not in clamd_conf, "clamd.conf must be TCP-only")
    require("StreamMaxLength" in clamd_conf, "clamd.conf must bound INSTREAM size")
    healthcheck = (CLAMD_DIR / "healthcheck.sh").read_text(encoding="utf-8")
    require("clamdscan" in healthcheck, "healthcheck must perform a real scan")
    entrypoint = (CLAMD_DIR / "entrypoint.sh").read_text(encoding="utf-8")
    require("freshclam" in entrypoint, "entrypoint must bootstrap signatures")
    require("exec clamd" in entrypoint, "entrypoint must exec clamd in foreground")

    # --- Caddy exposes metrics only through the token-protected API -----------
    caddy = CADDYFILE.read_text(encoding="utf-8")
    require(
        "/api/v1/admin/metrics" in caddy,
        "Caddyfile must route the Prometheus scrape path directly to the API",
    )

    # --- monitoring configuration is real and cannot drift from the app ------
    prom_dir = DEPLOY / "prometheus"
    prom_file = prom_dir / "prometheus.yml"
    rules_file = prom_dir / "alert.rules.yml"
    am_file = prom_dir / "alertmanager.yml"
    for path in (prom_file, rules_file, am_file):
        require(path.is_file(), f"missing monitoring config: {path.name}")
    if prom_file.is_file():
        prom_text = prom_file.read_text(encoding="utf-8")
        require("bearer_token_file" in prom_text, "scrape must use a scoped token file")
        require(
            "metrics_path: /api/v1/admin/metrics" in prom_text,
            "scrape must target the token-protected metrics path",
        )
        require("api:8000" in prom_text, "scrape must stay on the internal api network")
    if rules_file.is_file():
        rules_text = rules_file.read_text(encoding="utf-8")
        rule_data = _load_yaml(rules_file)
        expressions = [
            str(rule.get("expr", ""))
            for group in rule_data.get("groups", [])
            for rule in group.get("rules", [])
        ]
        require(len(expressions) >= 5, "alert rules must cover availability and security")
        emitted = set(
            re.findall(
                r"mafhum_[a-z0-9_]+",
                (
                    REPO_ROOT / "apps" / "api" / "src" / "mafhum_api" / "api" / "v1" / "admin.py"
                ).read_text(encoding="utf-8"),
            )
        )
        referenced = {
            name for expr in expressions for name in re.findall(r"mafhum_[a-z0-9_]+", expr)
        }
        require(
            referenced <= emitted,
            f"alert rules reference metrics the API never emits: {sorted(referenced - emitted)}",
        )
        require(
            "MafhumMalwareScannerUnavailable" in rules_text,
            "a fail-closed scanning outage must have an alert",
        )
    if am_file.is_file():
        require(
            "unconfigured" in am_file.read_text(encoding="utf-8"),
            "alertmanager must not pretend to deliver alerts without an owner destination",
        )

    return failures


def main() -> int:
    failures = check_stack()
    if failures:
        print(f"RESULT: {len(failures)} PRODUCTION COMPOSE CONTRACT VIOLATION(S)", flush=True)
        for item in failures:
            print(f"  - {item}", flush=True)
        return 1
    print("RESULT: ALL PRODUCTION COMPOSE CONTRACT CHECKS PASSED", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
