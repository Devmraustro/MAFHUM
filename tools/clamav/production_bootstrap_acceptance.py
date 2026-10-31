"""One-command execution of the production ClamAV container gate (Phase 2).

This is the operational gate that ``docs/OPERATIONAL_VERIFICATION_REPORT.md``
§2.3 describes step by step, automated so a Docker-capable host can run the
whole thing with one command and get a recorded, timed PASS/FAIL report:

  1. ``docker compose -f deploy/docker-compose.prod.yml config -q`` with the
     operator's env file (fail fast on template/config drift).
  2. Build the ``clamd`` image; record build time and image id.
  3. **Cold bootstrap with an empty signature volume**: remove the volume,
     start ``clamd``, poll the container health (a real bounded ``clamdscan``)
     every 15 s, and record container-start -> first ``healthy`` plus the
     freshclam download evidence from the logs.
  4. Start ``db`` + ``api`` (the api publishes 127.0.0.1:8000 through a
     generated, gitignored Compose override) and wait for
     ``/health/ready`` with ``malware_scanner=ok``.
  5. **Three-case acceptance through the real API**: clean upload accepted;
     EICAR-carrying DOCX rejected with ``malware_detected``; with the engine
     container stopped, uploads rejected with a ``scan_*`` reason — never
     ``CLEAN``, never ``malware_detected``. The uploads reuse the exact
     helpers from ``tools/acceptance/malware_fail_closed.py``.
  6. **Recovery**: restart the engine container; the signature volume must be
     reused (log line "reusing the signature database on the volume"), health
     must return without a fresh bootstrap, and a clean upload must be
     accepted again.
  7. **Refresh loop**: prove the 43 200 s (12 h) freshclam loop is alive by
     finding its ``sleep 43200`` process inside the container.
  8. **Second cold bootstrap** (``--full`` only): prove the cold path is
     repeatable by wiping the volume once more and timing it again.

Nothing here is mocked and no malware is used: the only infectious payload is
the published EICAR test string, and it is uploaded through the same HTTP
surface a browser would use.

Usage (on a host with Docker + Compose v2 and a Python that has httpx):

    python tools/clamav/production_bootstrap_acceptance.py --env-file deploy/.env.prod
    python tools/clamav/production_bootstrap_acceptance.py --env-file deploy/.env.prod --full

The env file must define the variables the production template requires
(``MAFHUM_DOMAIN``, ``MAFHUM_ACME_EMAIL``, ``POSTGRES_USER``,
``POSTGRES_PASSWORD``, ``SESSION_SECRET``, ``ALLOWED_ORIGINS``,
``METRICS_SCRAPE_TOKEN``); see ``deploy/.env.prod.example``. ``ALLOWED_ORIGINS``
is used as the Origin header for the acceptance uploads, so the value does not
need to resolve — it only has to match what the api expects.

Exit code 0 iff every phase passed. The report is written to
``.clamav-acceptance/report.md`` (gitignored).
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "deploy" / "docker-compose.prod.yml"
ACCEPTANCE = REPO_ROOT / "tools" / "acceptance" / "malware_fail_closed.py"
WORKDIR = REPO_ROOT / ".clamav-acceptance"
PROJECT = "mafhum-prod"
CONTAINER = f"{PROJECT}-clamd-1"
VOLUME = f"{PROJECT}_clamd_db"
HEALTH_POLL_SECONDS = 15
HEALTH_TIMEOUT_SECONDS = 1500  # compose start_period is 900 s; allow margin

REQUIRED_ENV = [
    "MAFHUM_DOMAIN",
    "MAFHUM_ACME_EMAIL",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "SESSION_SECRET",
    "ALLOWED_ORIGINS",
    "METRICS_SCRAPE_TOKEN",
]

failures: list[str] = []
passes = 0
report_lines: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    global passes
    mark = "PASS" if condition else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"[{mark}] {label}{suffix}", flush=True)
    report_lines.append(f"- [{'x' if condition else ' '}] {label}{suffix}")
    if condition:
        passes += 1
    else:
        failures.append(f"{label}{suffix}")
    return condition


def note(text: str) -> None:
    print(f"[note] {text}", flush=True)
    report_lines.append(f"- {text}")


def run(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float = 1800,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(  # noqa: S603 - fixed docker/python argv, operator host
        args,
        cwd=REPO_ROOT,
        env=merged_env,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )
    if capture and result.returncode != 0:
        print(result.stdout[-2000:] if result.stdout else "", flush=True)
        print(result.stderr[-2000:] if result.stderr else "", flush=True)
    return result


def compose(
    env: dict[str, str], *args: str, timeout: float = 1800
) -> subprocess.CompletedProcess[str]:
    return run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "--project-name", PROJECT, *args],
        env=env,
        timeout=timeout,
    )


def container_health(env: dict[str, str]) -> str:
    result = run(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", CONTAINER],
        env=env,
        timeout=60,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def container_logs(env: dict[str, str], since: str = "") -> str:
    args = ["docker", "logs", "--tail", "400"]
    if since:
        args += ["--since", since]
    args.append(CONTAINER)
    result = run(args, env=env, timeout=60)
    return (result.stdout or "") + (result.stderr or "")


def wait_healthy(env: dict[str, str], started_at: float, phase: str) -> tuple[bool, float]:
    while time.time() - started_at < HEALTH_TIMEOUT_SECONDS:
        state = container_health(env)
        if state == "healthy":
            return True, time.time() - started_at
        if state == "unhealthy" and time.time() - started_at > 900:
            # The compose start_period (900 s) has passed; compose itself
            # reports unhealthy only after retries are exhausted post-period.
            break
        time.sleep(HEALTH_POLL_SECONDS)
    check(f"{phase}: container reached healthy", False, container_health(env))
    return False, time.time() - started_at


def http_get(url: str) -> tuple[int, str]:
    try:
        response = httpx.get(url, timeout=15)
        return response.status_code, response.text
    except httpx.HTTPError as exc:
        return 0, str(exc)


def python_with_httpx() -> str:
    """Find an interpreter that can run the acceptance helpers (needs httpx)."""
    candidates = [
        REPO_ROOT / ".venv" / "bin" / "python",
        REPO_ROOT / ".venv" / "Scripts" / "python.exe",
        Path(sys.executable),
        Path(shutil.which("python3") or ""),
        Path(shutil.which("python") or ""),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            probe = run(
                [str(candidate), "-c", "import httpx"],
                timeout=60,
            )
            if probe.returncode == 0:
                return str(candidate)
    raise SystemExit(
        "No Python with httpx found. Install the backend dev dependencies "
        "(python -m pip install httpx) or create .venv first."
    )


ACCEPTANCE_SNIPPET = r"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "apps/api/src")
sys.path.insert(0, "apps/api")
sys.path.insert(0, "tools/acceptance")
import httpx

import malware_fail_closed as mf

api = sys.argv[1]
origin = sys.argv[2]
mode = sys.argv[3]
tmp = Path(tempfile.mkdtemp(prefix="mafhum-clamav-acc-"))
clean = mf.make_clean_pdf(tmp)
infected = mf.make_eicar_docx(tmp)
with httpx.Client(timeout=120.0) as client:
    ready = client.get(f"{api}/health/ready", timeout=30.0)
    checks = ready.json().get("checks", {}) if ready.status_code == 200 else {}
    expected = "ok" if mode == "engine-up" else "unavailable"
    mf.check(
        f"readiness reports malware_scanner={expected}",
        checks.get("malware_scanner") == expected,
        str(checks),
    )
    if not mf.register(client, api, origin):
        sys.exit(1)
    ok = mf.upload(client, api, origin, clean, mf.PDF_MIME)
    if mode == "engine-up":
        mf.check("clean upload accepted by the real engine", ok.status_code == 201,
                 f"HTTP {ok.status_code} {ok.text[:160]}")
    else:
        mf.check("clean upload REJECTED while the engine is down (fail-closed)",
                 ok.status_code == 422 and mf.reason_of(ok).startswith("scan_"),
                 f"HTTP {ok.status_code} reason={mf.reason_of(ok)}")
    bad = mf.upload(client, api, origin, infected, mf.DOCX_MIME)
    if mode == "engine-up":
        mf.check("EICAR DOCX rejected with malware_detected",
                 bad.status_code == 422 and mf.reason_of(bad) == "malware_detected",
                 f"HTTP {bad.status_code} reason={mf.reason_of(bad)}")
    else:
        mf.check("outage NEVER reports malware_detected or CLEAN",
                 bad.status_code == 422 and mf.reason_of(bad).startswith("scan_")
                 and mf.reason_of(bad) != "malware_detected",
                 f"HTTP {bad.status_code} reason={mf.reason_of(bad)}")
sys.exit(1 if mf.failures else 0)
"""


def run_acceptance(env: dict[str, str], python: str, api: str, origin: str, mode: str) -> bool:
    result = run(
        [python, "-c", ACCEPTANCE_SNIPPET, api, origin, mode],
        env=env,
        timeout=600,
    )
    return result.returncode == 0


def write_override() -> Path:
    """Publish the api on 127.0.0.1:8000 for the acceptance uploads."""
    override = WORKDIR / "override.yml"
    override.write_text(
        'services:\n  api:\n    ports:\n      - "127.0.0.1:8000:8000"\n',
        encoding="utf-8",
    )
    return override


def load_env(env_file: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--env-file", required=True, help="operator env file (see deploy/.env.prod.example)"
    )
    parser.add_argument(
        "--full", action="store_true", help="also run the second cold-bootstrap proof"
    )
    parser.add_argument("--keep-stack", action="store_true", help="leave the stack up afterwards")
    args = parser.parse_args(argv)

    env_file = Path(args.env_file)
    if not env_file.is_absolute():
        env_file = REPO_ROOT / env_file
    started = datetime.datetime.now(datetime.UTC)

    print("== MAFHUM production ClamAV gate (Phase 2) ==")
    docker_ok = shutil.which("docker") is not None
    check("docker CLI present", docker_ok)
    if not docker_ok:
        return 1
    compose_v2 = run(["docker", "compose", "version"], timeout=60)
    check(
        "docker compose v2 present",
        compose_v2.returncode == 0,
        (compose_v2.stdout or "").strip()[:80],
    )

    check("env file exists", env_file.is_file(), str(env_file))
    if not env_file.is_file():
        return 1
    env = load_env(env_file)
    missing = [key for key in REQUIRED_ENV if not env.get(key)]
    check("env file defines every required variable", not missing, f"missing: {missing}")
    if missing:
        return 1

    origin = env["ALLOWED_ORIGINS"].split(",")[0].strip()
    python = python_with_httpx()
    note(f"acceptance interpreter: {python}")

    if WORKDIR.exists():
        shutil.rmtree(WORKDIR, ignore_errors=True)
    WORKDIR.mkdir(parents=True)
    override = write_override()
    report_lines.append(f"# Production ClamAV gate — {started.isoformat()}")
    report_lines.append(f"- env file: `{env_file}`")
    report_lines.append(
        f"- compose: `{COMPOSE_FILE.name}` + generated override publishing 127.0.0.1:8000"
    )

    compose_env = {**os.environ, **env}

    # --- phase 0: config validation -------------------------------------------
    print("\n== phase 0: compose config validation ==")
    cfg = compose(
        compose_env,
        "-f",
        str(override),
        "--env-file",
        str(env_file),
        "config",
        "-q",
    )
    check("docker compose config -q passes with the operator env", cfg.returncode == 0)

    # --- phase 1: build ---------------------------------------------------------
    print("\n== phase 1: build the clamd image ==")
    build_started = time.time()
    build = compose(compose_env, "build", "clamd", timeout=3600)
    build_seconds = time.time() - build_started
    check("clamd image built", build.returncode == 0, f"{build_seconds:.0f} s")
    image_id = run(["docker", "images", "-q", "mafhum-clamd:0.3.0"], env=compose_env, timeout=60)
    note(f"image mafhum-clamd:0.3.0 id: {image_id.stdout.strip() or 'n/a'}")

    # --- phase 2: cold bootstrap --------------------------------------------------
    def cold_bootstrap(phase_label: str) -> bool:
        print(f"\n== {phase_label}: cold bootstrap with an EMPTY signature volume ==")
        compose(compose_env, "stop", "clamd", timeout=300)
        run(["docker", "volume", "rm", "-f", VOLUME], env=compose_env, timeout=120)
        boot_started = time.time()
        up = compose(
            compose_env,
            "-f",
            str(override),
            "--env-file",
            str(env_file),
            "up",
            "-d",
            "clamd",
            timeout=600,
        )
        if not check(f"{phase_label}: clamd container started", up.returncode == 0):
            return False
        healthy, elapsed = wait_healthy(compose_env, boot_started, phase_label)
        check(
            f"{phase_label}: SCAN-READY (healthy) reached",
            healthy,
            f"{elapsed:.0f} s from container start (healthcheck performs a real clamdscan)",
        )
        logs = container_logs(compose_env)
        check(
            f"{phase_label}: cold path logged the freshclam bootstrap",
            "no signature database on the volume" in logs,
        )
        download = [
            line
            for line in logs.splitlines()
            if "Downloading" in line or "MB" in line or "updated" in line.lower()
        ]
        note(
            f"{phase_label}: freshclam evidence: "
            + ("; ".join(download[-3:]) if download else "no download lines captured")
        )
        return healthy

    cold_ok = cold_bootstrap("phase 2")
    if not cold_ok:
        write_report(started)
        return 1

    # --- phase 3+4: stack up + engine-up acceptance ------------------------------
    print("\n== phase 3: start db + api and wait for readiness ==")
    up = compose(
        compose_env,
        "-f",
        str(override),
        "--env-file",
        str(env_file),
        "up",
        "-d",
        "db",
        "api",
        timeout=900,
    )
    check("db + api started", up.returncode == 0)
    api_ready = False
    for _ in range(60):
        status, body = http_get("http://127.0.0.1:8000/health/ready")
        if status == 200 and '"malware_scanner": "ok"' in body.replace("\n", " "):
            api_ready = True
            break
        time.sleep(5)
    check("api /health/ready reports malware_scanner=ok", api_ready)

    print("\n== phase 4: three-case acceptance (engine up) ==")
    engine_up_ok = run_acceptance(compose_env, python, "http://127.0.0.1:8000", origin, "engine-up")
    check(
        "engine-up acceptance: clean accepted + EICAR rejected (malware_detected)",
        engine_up_ok,
    )

    print("\n== phase 5: engine outage fails closed ==")
    stop = compose(compose_env, "stop", "clamd", timeout=300)
    check("clamd container stopped", stop.returncode == 0)
    time.sleep(5)
    outage_ok = run_acceptance(
        compose_env, python, "http://127.0.0.1:8000", origin, "engine-outage"
    )
    check(
        "engine-outage acceptance: uploads rejected with scan_* "
        "(never CLEAN, never malware_detected)",
        outage_ok,
    )

    print("\n== phase 6: recovery reuses the signature volume ==")
    boot_started = time.time()
    start = compose(compose_env, "start", "clamd", timeout=300)
    check("clamd container restarted", start.returncode == 0)
    healthy, elapsed = wait_healthy(compose_env, boot_started, "phase 6")
    check("health returned after restart", healthy, f"{elapsed:.0f} s")
    logs = container_logs(compose_env)
    check(
        "signature volume REUSED (no fresh bootstrap)",
        "reusing the signature database on the volume" in logs,
    )
    recovered_ok = run_acceptance(compose_env, python, "http://127.0.0.1:8000", origin, "engine-up")
    check("clean upload accepted again after recovery", recovered_ok)

    print("\n== phase 7: 12-hour refresh loop is alive ==")
    probe = run(
        [
            "docker",
            "exec",
            CONTAINER,
            "sh",
            "-c",
            "for p in /proc/[0-9]*/cmdline; "
            'do tr "\\0" " " < "$p" 2>/dev/null; echo; done | grep -q "sleep 43200"',
        ],
        env=compose_env,
        timeout=60,
    )
    check(
        "freshclam refresh loop (sleep 43200) running in the container",
        probe.returncode == 0,
        "verified via /proc cmdlines; the 12 h cadence itself is verified by "
        "inspecting `docker logs` after 12 h of uptime",
    )

    if args.full:
        print("\n== phase 8 (--full): second cold bootstrap ==")
        second_ok = cold_bootstrap("phase 8")
        check("second cold bootstrap reached SCAN-READY", second_ok)

    if not args.keep_stack:
        print("\n== teardown ==")
        down = compose(compose_env, "down", timeout=600)
        check("stack brought down (volumes preserved)", down.returncode == 0)
    else:
        note("--keep-stack: containers left running")

    write_report(started)
    print(f"\n== RESULT: {passes} passed, {len(failures)} failed ==")
    for failure in failures:
        print(f"  FAIL: {failure}")
    print(f"[report] {WORKDIR / 'report.md'}")
    return 0 if not failures else 1


def write_report(started: datetime.datetime) -> None:
    elapsed = (datetime.datetime.now(datetime.UTC) - started).total_seconds()
    report_lines.append("")
    report_lines.append(
        f"**Result: {passes} passed, {len(failures)} failed "
        f"({elapsed:.0f} s total, {datetime.datetime.now(datetime.UTC).isoformat()}).**"
    )
    if failures:
        report_lines.append("")
        report_lines.append("## Failures")
        report_lines.extend(f"- {failure}" for failure in failures)
    (WORKDIR / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
