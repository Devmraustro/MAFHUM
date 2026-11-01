"""Isolated acceptance gate for the production ClamAV container (Phase 2) — SAFETY REMEDIATION.

This tool automates the operational gate described in
``docs/OPERATIONAL_VERIFICATION_REPORT.md`` §2.3 **without ever being able to
touch a real deployment**. It exists because running acceptance against a
production Compose project is how an acceptance tool becomes an outage: the
production template (``deploy/docker-compose.prod.yml``) declares
``name: mafhum-prod``; a tool that reuses that project name can stop the
production containers and delete the production volumes.

Safety model (enforced structurally, not by convention)
-------------------------------------------------------
1. **Unique isolated project per run.** Every execution creates its own
   Compose project named ``mafhum-clamav-acc-<timestamp>-<nonce>``. The name
   is generated, never taken from the environment or the Compose file, and can
   never equal ``mafhum-prod`` (or the dev default ``mafhum``) — those names
   are reserved and rejected outright, including by prefix.
2. **No hardcoded production identifiers.** Container and volume names are
   derived from the isolated project name (``<project>-clamd-1``,
   ``<project>_clamd_db``); the only ``mafhum-prod`` literals in this file
   belong to the reserved-name guard (and this explanation of it).
3. **Foreign resources are never addressed.** Every ``docker compose``
   invocation passes ``--project-name <isolated>`` through a single wrapper
   (``DockerCli.compose``), so no compose command can affect another project.
   No stop/restart/remove/inspect is ever issued for a resource outside the
   isolated project, and image builds are tagged with run-unique tags so even
   the local image cache of a production host is not rewritten.
4. **Volume deletion is triple-gated and opt-in.** A volume is deleted only
   when ALL hold: (a) this run created it (tracked set, seeded by a preflight
   proof that nothing with this project label existed), (b) its Compose
   ownership label (``com.docker.compose.project``) matches the isolated
   project, and (c) its exact name is re-verified by ``docker volume inspect``
   immediately before the removal. Deletion is additionally unreachable
   without ``--full`` **and** ``--confirm-isolated-test``, and it is attempted
   only after this run's own container stop and removal both provably
   succeeded — a failed stop or removal aborts the destructive sub-operation
   and leaves the volume in place. Volumes are never deleted as cleanup.
5. **Default execution is non-destructive.** Without those flags, no volume is
   ever removed — the run starts from a brand-new project, so its first
   bootstrap is already a cold one. Cleanup brings the isolated project down
   **without** ``-v`` (volumes are preserved as evidence).
6. **Preflight aborts on ambiguity.** Docker/Compose availability, project-name
   uniqueness (``docker compose ls``), container/volume label queries, and
   resource-name collisions are all checked before anything is created; any
   failure or ambiguity aborts with exit code 2 before mutation. The preflight
   also refuses remote Docker Engines: the effective endpoint is resolved with
   the CLI's precedence (``DOCKER_HOST`` first, then ``DOCKER_CONTEXT`` or the
   persisted context, whose endpoint is read from ``docker context inspect``
   — never inferred from the context name), and anything other than a
   ``unix://`` socket or a Windows ``npipe:`` named pipe aborts the run.
7. **Failure cleanup is never destructive, and phases fail fast.** A failed
   config validation, image build or cold bootstrap prevents every dependent
   phase (including the acceptance uploads). A cold bootstrap counts as
   successful only when the container reached SCAN-READY **and** the logs
   prove the fresh-database path ran — health alone is not cold evidence.
   A failed engine stop or restart — or a restart after which health does
   not return to SCAN-READY — similarly skips the uploads, evidence checks
   and probes that depend on it. ``try/finally`` brings down only the
   isolated project; volumes are never deleted during cleanup, so failure
   evidence survives.
8. **Loopback-only API exposure, with engine and ownership proofs.** The
   generated Compose override binds the api to ``127.0.0.1:<port>``
   (default 8000, ``--api-port`` to change). Before anything is created the
   preflight proves the port is free and the engine is local; the acceptance
   uploads run only when this run's ``up`` succeeded, its own api answered
   readiness, and ``docker inspect`` proved that the api container carries
   this run's exact name and project label and owns the
   ``127.0.0.1:<port> -> 8000/tcp`` host binding. A foreign local service
   can therefore never receive the uploads: it cannot hold the port once
   Docker bound it, and readiness alone authorises nothing.
9. **Reports are redacted.** Every check detail, note and failure reason
   passes through a redactor that replaces each env-file value with
   ``***REDACTED***``, so a docker error echoing a configured value can never
   reach the report.
10. **Self-audit.** After cleanup the tool scans every docker invocation it
    made: if any targeted a production identifier, or any compose command
    lacked the isolated ``--project-name``, the run is marked FAILED even if
    the commands themselves succeeded.

What the run does
-----------------
config validation, timed image build (unique tag), cold bootstrap with health
polling to SCAN-READY, db+api start, the three-case acceptance through the
real API (clean accepted; EICAR DOCX rejected ``malware_detected``; engine
stopped → ``scan_*``, never CLEAN, never ``malware_detected`` — reusing the
exact helpers of ``tools/acceptance/malware_fail_closed.py``), recovery with
signature-volume reuse, the 43 200 s refresh-loop liveness probe, and — only
with ``--full --confirm-isolated-test`` — a second cold bootstrap via the
triple-gated volume deletion.

Nothing is mocked on a real run and no malware is used: the only infectious
payload is the published EICAR test string, uploaded through the same HTTP
surface a browser would use.

Usage (Docker + Compose v2 host, Python with httpx installed):

    python tools/clamav/production_bootstrap_acceptance.py --env-file deploy/.env.prod
    python tools/clamav/production_bootstrap_acceptance.py --env-file deploy/.env.prod \
        --full --confirm-isolated-test

The env file must define the variables the production template requires
(``MAFHUM_DOMAIN``, ``MAFHUM_ACME_EMAIL``, ``POSTGRES_USER``,
``POSTGRES_PASSWORD``, ``SESSION_SECRET``, ``ALLOWED_ORIGINS``,
``METRICS_SCRAPE_TOKEN``) — see ``deploy/.env.prod.example``. ``ALLOWED_ORIGINS``
is used as the Origin header for the acceptance uploads; it only has to match
what the api expects, it does not need to resolve.

``--api-port`` selects the loopback port (default 8000; the development
Compose stack uses 8000, so pick another port when it is running). The tool
requires a **local** Docker Engine: a ``DOCKER_HOST``/``DOCKER_CONTEXT``
pointing at a remote engine aborts the run before anything is created. The
env file is read only as configuration input; none of its values are printed
or written to the report (recorded command output is redacted against them).
``--keep-stack`` skips the final ``down`` and leaves the isolated stack
running for inspection — no volume is deleted either way, and the run prints
the exact, fully qualified ``docker compose --project-name <project> -f
<compose> --env-file <env> -f <override> down`` command that reproduces the
run's configuration; add ``-v`` to it only if you also want the isolated
volumes gone.

Exit codes: 0 = all checks passed; 1 = at least one check failed; 2 = preflight
aborted (nothing was created). The timed report is written to
``.clamav-acceptance/<isolated-project>/report.md`` (gitignored).

Safety tests live in ``apps/api/tests/test_clamav_acceptance_safety.py`` and
run in CI on every commit with a mocked Docker CLI — they do not need Docker.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "deploy" / "docker-compose.prod.yml"
WORKDIR_ROOT = REPO_ROOT / ".clamav-acceptance"

PROJECT_PREFIX = "mafhum-clamav-acc"
# The production Compose project (deploy/docker-compose.prod.yml declares
# `name: mafhum-prod`) and the root development Compose project (defaults to
# the directory name "mafhum") must never be addressable by this tool.
RESERVED_PROJECT_NAMES = frozenset({"mafhum-prod", "mafhum"})
RESERVED_PROJECT_PREFIX = "mafhum-prod"
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
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


# --------------------------------------------------------------------- results
@dataclass(frozen=True)
class CommandResult:
    """Outcome of one (possibly mocked) command invocation."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


def _subprocess_runner(args: list[str], timeout: float = 1800) -> CommandResult:
    """The real command runner. Never raises; failures become results."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed docker/python argv, operator host
            args,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return CommandResult(127, "", f"executable not found: {exc}")
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", "command timed out")
    return CommandResult(result.returncode, result.stdout or "", result.stderr or "")


# ------------------------------------------------------------------- isolation
def is_reserved_project_name(name: str) -> bool:
    """True for any project name this tool must never use or address."""
    normalised = name.strip().lower()
    if normalised in RESERVED_PROJECT_NAMES:
        return True
    return normalised.startswith(RESERVED_PROJECT_PREFIX + "-") or normalised.startswith(
        RESERVED_PROJECT_PREFIX + "_"
    )


def is_production_resource_identifier(element: str) -> bool:
    """True if a command argument names a production project/container/volume.

    Used by the post-run self-audit and by the safety tests. Deliberately
    matches identifier shapes (exact name, ``mafhum-prod-*`` containers and
    networks, ``mafhum-prod_*`` volumes) rather than raw substrings, so a
    checkout directory that happens to contain "mafhum-prod" in a file path
    cannot produce a false accusation.
    """
    normalised = element.strip().lower()
    if normalised in RESERVED_PROJECT_NAMES:
        return True
    return normalised.startswith(RESERVED_PROJECT_PREFIX + "-") or normalised.startswith(
        RESERVED_PROJECT_PREFIX + "_"
    )


def build_project_name(now: datetime.datetime | None = None, nonce: str | None = None) -> str:
    """Generate a unique, never-reserved isolated project name."""
    moment = now or datetime.datetime.now(datetime.UTC)
    suffix = (nonce or secrets.token_hex(4)).lower()
    name = f"{PROJECT_PREFIX}-{moment.strftime('%Y%m%d-%H%M%S')}-{suffix}"
    if is_reserved_project_name(name):  # pragma: no cover - structurally impossible
        raise RuntimeError(f"generated a reserved project name: {name!r}")
    return name


@dataclass(frozen=True)
class IsolationPlan:
    """Everything the run is allowed to touch, derived from one unique name."""

    project: str
    api_port: int = 8000
    nonce: str = "local"

    def __post_init__(self) -> None:
        if is_reserved_project_name(self.project):
            raise ValueError(f"refusing reserved project name: {self.project!r}")
        if not (1 <= self.api_port <= 65535):
            raise ValueError(f"api port out of range: {self.api_port}")

    @property
    def clamd_container(self) -> str:
        return f"{self.project}-clamd-1"

    @property
    def api_container(self) -> str:
        return f"{self.project}-api-1"

    @property
    def clamd_volume(self) -> str:
        return f"{self.project}_clamd_db"

    @property
    def clamd_image(self) -> str:
        return f"mafhum-clamd-acc:{self.nonce}"

    @property
    def api_image(self) -> str:
        return f"mafhum-api-acc:{self.nonce}"

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"


# ------------------------------------------------------------------ docker CLI
class DockerCli:
    """Thin, mockable wrapper over the docker CLI.

    Every invocation is recorded in ``invocations`` so the run can audit
    itself and the safety tests can prove which commands were (not) issued.
    Compose commands are only available through :meth:`compose`, which always
    injects the isolated ``--project-name`` — there is no code path that can
    issue a compose command against another project.
    """

    def __init__(self, runner: Callable[[list[str], float], CommandResult] | None = None) -> None:
        self._runner = runner or _subprocess_runner
        self.invocations: list[list[str]] = []

    def _run(self, args: list[str], timeout: float = 1800) -> CommandResult:
        self.invocations.append(list(args))
        return self._runner(args, timeout)

    # -- compose (always project-scoped) --------------------------------------
    def compose(self, project: str, args: list[str], timeout: float = 1800) -> CommandResult:
        if is_reserved_project_name(project):
            raise ValueError(f"refusing compose command for reserved project {project!r}")
        return self._run(["docker", "compose", "--project-name", project, *args], timeout)

    # -- read-only probes ------------------------------------------------------
    def version(self) -> CommandResult:
        return self._run(["docker", "--version"], 60)

    def compose_version(self) -> CommandResult:
        return self._run(["docker", "compose", "version"], 60)

    def compose_projects(self) -> CommandResult:
        """Names of every Compose project on this host (``docker compose ls``)."""
        return self._run(["docker", "compose", "ls", "--all", "--format", "json"], 60)

    def context_show(self) -> CommandResult:
        """Name of the active Docker context (may be ``default``)."""
        return self._run(["docker", "context", "show"], 60)

    def context_inspect(self, name: str) -> CommandResult:
        """Full JSON of one Docker context, incl. its engine endpoint."""
        return self._run(["docker", "context", "inspect", name], 60)

    def container_inspect(self, container: str) -> CommandResult:
        """Full JSON of one container (identity, labels, state, port bindings)."""
        return self._run(["docker", "inspect", container], 60)

    def containers_with_label(self, project: str) -> CommandResult:
        """Container names carrying this project's ownership label."""
        return self._run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label={COMPOSE_PROJECT_LABEL}={project}",
                "--format",
                "{{.Names}}",
            ],
            60,
        )

    def volumes_with_label(self, project: str) -> CommandResult:
        """Volume names carrying this project's ownership label."""
        return self._run(
            [
                "docker",
                "volume",
                "ls",
                "--filter",
                f"label={COMPOSE_PROJECT_LABEL}={project}",
                "--format",
                "{{.Name}}",
            ],
            60,
        )

    def container_name_exists(self, name: str) -> CommandResult:
        """Names of containers exactly matching ``name`` (empty = none)."""
        return self._run(
            ["docker", "ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"],
            60,
        )

    def volume_inspect(self, name: str) -> CommandResult:
        return self._run(["docker", "volume", "inspect", name], 60)

    def container_health(self, container: str) -> CommandResult:
        return self._run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", container], 60
        )

    def container_logs(self, container: str) -> CommandResult:
        result = self._run(["docker", "logs", "--tail", "400", container], 60)
        return result

    # -- mutations (isolated resources only) ------------------------------------
    def volume_rm(self, name: str) -> CommandResult:
        # Fail closed at the lowest layer: the call-site triple gate and the
        # post-run audit are necessary but not sufficient — the destructive
        # primitive itself must refuse a production identifier BEFORE the
        # command is recorded or executed, so a future call site that forgets
        # the gate still cannot touch production.
        if is_production_resource_identifier(name):
            raise ValueError(f"refusing to remove a production volume: {name!r}")
        return self._run(["docker", "volume", "rm", name], 120)

    def exec_refresh_probe(self, container: str) -> CommandResult:
        """Prove the 12 h freshclam loop is alive inside OUR container."""
        # Same rule for exec: only this run's isolated containers are ever
        # addressable, enforced before the command is recorded or executed.
        if is_production_resource_identifier(container):
            raise ValueError(f"refusing to exec into a production container: {container!r}")
        script = (
            "for p in /proc/[0-9]*/cmdline; "
            'do tr "\\0" " " < "$p" 2>/dev/null; echo; done | grep -q "sleep 43200"'
        )
        return self._run(["docker", "exec", container, "sh", "-c", script], 60)


def _lines(result: CommandResult) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.strip()]


def _volume_missing(result: CommandResult) -> bool:
    return result.returncode != 0 and "no such volume" in result.stderr.lower()


# -------------------------------------------------------- engine locality gate
# Fail-closed: only these endpoint schemes prove the engine is local. Anything
# else — tcp://, http(s)://, ssh://, fd://, or an empty endpoint — is refused,
# because with a remote engine `compose up` succeeds remotely while
# 127.0.0.1:<port> on this machine is NOT bound by the run's container.
LOCAL_ENDPOINT_PREFIXES = ("unix://", "npipe:")  # npipe = Windows named pipe


def is_local_endpoint(endpoint: str) -> bool:
    """True only for endpoint schemes that prove a local Docker Engine."""
    normalised = endpoint.strip().lower()
    return normalised.startswith(LOCAL_ENDPOINT_PREFIXES)


def resolve_engine_endpoint(
    docker: DockerCli,
    env: Mapping[str, str] | None = None,
) -> tuple[str, str, list[str]]:
    """Resolve the effective Docker Engine endpoint like the docker CLI does.

    Precedence (mirrors the CLI): the ``DOCKER_HOST`` environment variable
    first, then the context selected by ``DOCKER_CONTEXT`` (or the persisted
    ``docker context show``), whose endpoint is read from ``docker context
    inspect`` — the *endpoint*, never the context *name*, decides locality.

    Returns ``(endpoint, source, problems)``; problems mean "unknown, abort".
    """
    effective = os.environ if env is None else env
    problems: list[str] = []

    docker_host = (effective.get("DOCKER_HOST") or "").strip()
    if docker_host:
        return docker_host, "DOCKER_HOST", problems

    context_name = (effective.get("DOCKER_CONTEXT") or "").strip()
    if not context_name:
        shown = docker.context_show()
        if shown.returncode != 0:
            return (
                "",
                "",
                [
                    "cannot determine the active Docker context (docker context show "
                    "failed): " + shown.stderr.strip()[:120]
                ],
            )
        context_name = shown.stdout.strip() or "default"

    inspected = docker.context_inspect(context_name)
    if inspected.returncode != 0:
        return (
            "",
            "",
            [f"cannot inspect Docker context {context_name!r}: " + inspected.stderr.strip()[:120]],
        )
    try:
        endpoint = str(json.loads(inspected.stdout)[0]["Endpoints"]["docker"]["Host"])
    except (ValueError, IndexError, KeyError, TypeError):
        return "", "", [f"Docker context {context_name!r} has an unparseable engine endpoint"]
    return endpoint, f"context {context_name!r}", problems


# ------------------------------------------------------- api ownership proof
def verify_api_ownership(docker: DockerCli, plan: IsolationPlan) -> tuple[bool, str]:
    """Prove the api listener on 127.0.0.1:<port> is this run's container.

    Checks the container's exact name, its Compose ownership label, that it is
    running, and that Docker published 127.0.0.1:<port> for its 8000/tcp port.
    A local unrelated service answering readiness can never pass this gate:
    the gate is about who owns the port binding, not about who answers.
    """
    result = docker.container_inspect(plan.api_container)
    if result.returncode != 0:
        return False, f"cannot inspect the api container (rc={result.returncode})"
    try:
        info = json.loads(result.stdout)[0]
    except (ValueError, IndexError, TypeError):
        return False, "api container inspect output was unparseable"
    if info.get("Name") != f"/{plan.api_container}":
        return False, "api container identity mismatch"
    labels = (info.get("Config") or {}).get("Labels") or {}
    if labels.get(COMPOSE_PROJECT_LABEL) != plan.project:
        return False, "api container ownership label does not match the isolated project"
    if not (info.get("State") or {}).get("Running"):
        return False, "api container is not running"
    bindings = ((info.get("NetworkSettings") or {}).get("Ports") or {}).get("8000/tcp")
    bindings = bindings or []
    expected = {"HostIp": "127.0.0.1", "HostPort": str(plan.api_port)}
    if expected not in bindings:
        return False, (
            f"the isolated api does not own the 127.0.0.1:{plan.api_port} host binding: {bindings}"
        )
    return True, ""


# ------------------------------------------------------------- secret redaction
# Env keys whose values are secrets by nature: redacted at ANY length, so a
# pathologically short password cannot slip through the general >=4-char rule.
SENSITIVE_KEY_MARKERS = ("secret", "password", "token", "api_key", "privatekey")


def _redactable_values(env: Mapping[str, str]) -> list[str]:
    """Env values that must never appear in recorded output.

    Every value of at least 4 characters, plus every value of a
    sensitive-named key regardless of length (short passwords included).
    Values of ordinary short keys (e.g. a 2-character username) are left
    alone so ordinary words are not mangled.
    """
    values = {v for v in env.values() if v and len(v) >= 4}
    values |= {
        value
        for key, value in env.items()
        if value and any(marker in key.lower() for marker in SENSITIVE_KEY_MARKERS)
    }
    return sorted(values, key=len, reverse=True)


def redact_env_values(text: str, env: Mapping[str, str], protect: tuple[str, ...] = ()) -> str:
    """Replace every configured env-file value in ``text`` with a placeholder.

    Applied to everything the Reporter records (check details, notes, failure
    reasons), so an excerpt of docker output that happens to echo an env-file
    value can never reach the report. Ordinary values shorter than 4
    characters are skipped to avoid mangling ordinary words — but values of
    sensitive-named keys (secret/password/token/...) are redacted at any
    length.

    ``protect`` lists the run's own generated tokens (project, container,
    volume and image names, the api url). The text is split on those tokens
    and only the unprotected segments are redacted, so that, e.g., a short
    ``POSTGRES_USER`` value that happens to be a substring of the generated
    project name cannot corrupt the reproducible cleanup command. Protected
    tokens are generated by this tool and provably contain no env-file values.
    """
    values = _redactable_values(env)
    if not values:
        return text
    # Split the text into (segment, guarded) parts along the protected tokens.
    parts: list[tuple[str, bool]] = [(text, False)]
    for token in protect:
        if not token:
            continue
        rebuilt: list[tuple[str, bool]] = []
        for chunk, guarded in parts:
            if guarded:
                rebuilt.append((chunk, True))
                continue
            pieces = chunk.split(token)
            for index, piece in enumerate(pieces):
                rebuilt.append((piece, False))
                if index < len(pieces) - 1:
                    rebuilt.append((token, True))
        parts = rebuilt
    out: list[str] = []
    for chunk, guarded in parts:
        if guarded:
            out.append(chunk)
            continue
        redacted = chunk
        for value in values:
            redacted = redacted.replace(value, "***REDACTED***")
        out.append(redacted)
    return "".join(out)


# ------------------------------------------------------------------- preflight
def loopback_port_free(port: int) -> bool:
    """True when nothing is listening on 127.0.0.1:<port> (no payload sent).

    A bare TCP connect attempt: if a connection succeeds, some other service
    already owns the loopback port and the acceptance run must not use it —
    not only would ``compose up`` fail to publish it, the readiness probe and
    the acceptance uploads could otherwise reach a stack this run did not
    create (the development Compose stack publishes 127.0.0.1:8000:8000, the
    tool's default port).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(2)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def preflight(
    docker: DockerCli,
    plan: IsolationPlan,
    port_free: Callable[[int], bool] | None = None,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Prove the isolated project is safe to create. Returns problems.

    Read-only: no container, volume, network or image is created, stopped,
    removed or modified here. Any ambiguity aborts the run (exit 2) before
    mutation. Resources of OTHER projects are never inspected: every query is
    scoped to the generated unique name or its ownership label. ``env`` is the
    process environment used to resolve the Docker engine endpoint
    (``DOCKER_HOST`` / ``DOCKER_CONTEXT``); it defaults to ``os.environ``.
    """
    problems: list[str] = []
    check_port = port_free or loopback_port_free

    # Reserved names are rejected before ANY docker call is made, so a
    # production project name can never even be probed.
    if is_reserved_project_name(plan.project):
        problems.append(
            f"project name {plan.project!r} is reserved for the real deployment "
            "and can never be used by the acceptance tool"
        )
        return problems

    # The loopback port must be free before anything is created: if another
    # service owns it, the acceptance uploads could reach a stack this run did
    # not start. Checked before any docker call so the abort costs nothing.
    if not check_port(plan.api_port):
        problems.append(
            f"127.0.0.1:{plan.api_port} is already in use by another service; "
            "pass --api-port <free port> (the development stack uses 8000)"
        )
        return problems

    version = docker.version()
    if version.returncode != 0:
        problems.append(f"docker CLI unavailable: {version.stderr.strip()[:200]}")
        return problems
    compose_version = docker.compose_version()
    if compose_version.returncode != 0:
        problems.append(f"docker compose v2 unavailable: {compose_version.stderr.strip()[:200]}")
        return problems

    # Remote engines are refused outright (fail-closed). With DOCKER_HOST /
    # DOCKER_CONTEXT pointing at a remote engine, `compose up` succeeds
    # remotely while 127.0.0.1:<port> on THIS machine is bound by no one (or
    # by an unrelated local service), so the ownership proof below would be
    # meaningless. Locality is decided by the resolved endpoint, never by the
    # context name.
    endpoint, source, endpoint_problems = resolve_engine_endpoint(docker, env)
    if endpoint_problems:
        return problems + endpoint_problems
    if not is_local_endpoint(endpoint):
        problems.append(
            f"remote Docker engine refused: endpoint {endpoint!r} (from {source}); "
            "this tool requires a local engine (unix socket or Windows named "
            "pipe). Run it on the Docker host itself."
        )
        return problems

    projects = docker.compose_projects()
    if projects.returncode != 0:
        problems.append("cannot enumerate Compose projects (docker compose ls failed); aborting")
        return problems
    try:
        existing = {entry.get("Name", "") for entry in json.loads(projects.stdout)}
    except ValueError:
        problems.append("docker compose ls produced unparseable output; aborting")
        return problems
    if plan.project in existing:
        problems.append(f"project-name collision: {plan.project!r} already exists on this host")

    labelled_containers = docker.containers_with_label(plan.project)
    if labelled_containers.returncode != 0:
        problems.append("cannot query containers by ownership label; aborting")
    elif _lines(labelled_containers):
        problems.append(
            "ambiguity: containers already carry this run's project label: "
            + ", ".join(_lines(labelled_containers))
        )

    labelled_volumes = docker.volumes_with_label(plan.project)
    if labelled_volumes.returncode != 0:
        problems.append("cannot query volumes by ownership label; aborting")
    elif _lines(labelled_volumes):
        problems.append(
            "ambiguity: volumes already carry this run's project label: "
            + ", ".join(_lines(labelled_volumes))
        )

    container_collision = docker.container_name_exists(plan.clamd_container)
    if container_collision.returncode != 0:
        problems.append("cannot check container-name collisions; aborting")
    elif _lines(container_collision):
        problems.append(f"container name collision: {plan.clamd_container!r} already exists")

    volume_state = docker.volume_inspect(plan.clamd_volume)
    if not _volume_missing(volume_state):
        problems.append(f"volume name collision or ambiguous state for {plan.clamd_volume!r}")

    return problems


# ------------------------------------------------------- volume deletion gate
def volume_is_deletable(
    docker: DockerCli,
    volume_name: str,
    plan: IsolationPlan,
    created_by_this_run: set[str],
) -> tuple[bool, str]:
    """Triple gate: created by this run + ownership label + exact name.

    This function performs the verification; :func:`delete_volume_safely`
    calls it immediately before the actual removal so the name and ownership
    are re-verified at the last possible moment.
    """
    if is_production_resource_identifier(volume_name):
        return False, "refusing: volume name is a production identifier"
    if volume_name not in created_by_this_run:
        return False, "refusing: volume was not created by this run"
    inspect = docker.volume_inspect(volume_name)
    if inspect.returncode != 0:
        return False, f"cannot inspect volume (rc={inspect.returncode})"
    try:
        info = json.loads(inspect.stdout)[0]
    except (ValueError, IndexError, KeyError):
        return False, "volume inspect output was unparseable"
    if info.get("Name") != volume_name:
        return False, "exact-name verification failed"
    if (info.get("Labels") or {}).get(COMPOSE_PROJECT_LABEL) != plan.project:
        return False, "ownership label does not match this run's isolated project"
    return True, ""


def delete_volume_safely(
    docker: DockerCli,
    volume_name: str,
    plan: IsolationPlan,
    created_by_this_run: set[str],
) -> tuple[bool, str]:
    """Delete one volume only if every gate passes, re-verified right now."""
    allowed, why = volume_is_deletable(docker, volume_name, plan, created_by_this_run)
    if not allowed:
        return False, why
    result = docker.volume_rm(volume_name)
    if result.returncode != 0:
        return False, f"docker volume rm failed: {result.stderr.strip()[:200]}"
    return True, ""


# ------------------------------------------------------------------- self-audit
def audit_invocations(invocations: list[list[str]], plan: IsolationPlan) -> list[str]:
    """Post-run audit: prove no command could have targeted production."""
    problems: list[str] = []
    for argv in invocations:
        for element in argv:
            if is_production_resource_identifier(element):
                problems.append(f"command addressed a production identifier: {element!r} in {argv}")
        if argv[:2] == ["docker", "compose"]:
            # `docker compose version` and `docker compose ls` are host-level
            # read-only probes; every other compose command (all issued through
            # DockerCli.compose) must carry the isolated --project-name.
            host_level = len(argv) > 2 and argv[2] in ("version", "ls")
            if "--project-name" in argv:
                try:
                    targeted = argv[argv.index("--project-name") + 1]
                except IndexError:
                    targeted = "<missing>"
                if targeted != plan.project:
                    problems.append(
                        f"compose command targeted project {targeted!r}, expected {plan.project!r}"
                    )
            elif not host_level:
                problems.append(f"compose command without --project-name: {argv}")
    return problems


# -------------------------------------------------------------------- reporting
def _no_redaction(text: str) -> str:
    return text


@dataclass
class Reporter:
    passes: int = 0
    failures: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    # Every recorded detail/note passes through this before it reaches the
    # report or stdout, so configured env-file values are always redacted.
    redactor: Callable[[str], str] = field(default=_no_redaction)

    def check(self, label: str, condition: bool, detail: str = "") -> bool:
        mark = "PASS" if condition else "FAIL"
        safe_detail = self.redactor(detail)
        suffix = f" — {safe_detail}" if safe_detail else ""
        print(f"[{mark}] {label}{suffix}", flush=True)
        self.lines.append(f"- [{'x' if condition else ' '}] {label}{suffix}")
        if condition:
            self.passes += 1
        else:
            self.failures.append(f"{label}{suffix}")
        return condition

    def note(self, text: str) -> None:
        safe_text = self.redactor(text)
        print(f"[note] {safe_text}", flush=True)
        self.lines.append(f"- {safe_text}")


# ------------------------------------------------------------- override / env
def write_override(workdir: Path, plan: IsolationPlan) -> Path:
    """Generate the run's Compose override: loopback API bind + unique tags.

    Unique image tags mean even ``docker compose build`` cannot overwrite the
    image tags a production deployment uses; the api is published on the
    loopback interface only.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    override = workdir / "override.yml"
    override.write_text(
        "services:\n"
        "  clamd:\n"
        f"    image: {plan.clamd_image}\n"
        "  api:\n"
        f"    image: {plan.api_image}\n"
        "    ports:\n"
        f'      - "127.0.0.1:{plan.api_port}:8000"\n',
        encoding="utf-8",
    )
    return override


def compose_flags(env_file: Path, override: Path) -> list[str]:
    return ["-f", str(COMPOSE_FILE), "--env-file", str(env_file), "-f", str(override)]


def load_env(env_file: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def validate_env_file(env_file: Path) -> list[str]:
    if not env_file.is_file():
        return [f"env file not found: {env_file}"]
    env = load_env(env_file)
    missing = [key for key in REQUIRED_ENV if not env.get(key)]
    if missing:
        return [f"env file is missing required variables: {missing}"]
    return []


# ----------------------------------------------------------------- http probes
def http_get(url: str) -> tuple[int, str]:
    """Readiness probe against the loopback-published api."""
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
            probe = _subprocess_runner([str(candidate), "-c", "import httpx"], 60)
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
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if mf.failures else 0)
"""


def _default_acceptance_runner(python: str, api: str, origin: str, mode: str) -> CommandResult:
    return _subprocess_runner([python, "-c", ACCEPTANCE_SNIPPET, api, origin, mode], 600)


# ------------------------------------------------------------------- the gates
def wait_healthy(
    docker: DockerCli,
    plan: IsolationPlan,
    started_at: float,
    phase: str,
    reporter: Reporter,
    sleep: Callable[[float], None],
) -> tuple[bool, float]:
    while time.time() - started_at < HEALTH_TIMEOUT_SECONDS:
        result = docker.container_health(plan.clamd_container)
        state = result.stdout.strip() if result.returncode == 0 else "unknown"
        if state == "healthy":
            return True, time.time() - started_at
        sleep(HEALTH_POLL_SECONDS)
    reporter.check(f"{phase}: container reached healthy", False, "timeout")
    return False, time.time() - started_at


def cold_bootstrap(
    docker: DockerCli,
    plan: IsolationPlan,
    flags: list[str],
    phase: str,
    reporter: Reporter,
    sleep: Callable[[float], None],
) -> bool:
    """Start the engine on a signature volume that did not exist before.

    The isolated project is brand new, so its first ``up`` is by construction
    a cold bootstrap — nothing is deleted here.
    """
    boot_started = time.time()
    up = docker.compose(plan.project, [*flags, "up", "-d", "clamd"], timeout=600)
    if not reporter.check(
        f"{phase}: clamd container started", up.returncode == 0, up.stderr.strip()[:200]
    ):
        return False
    healthy, elapsed = wait_healthy(docker, plan, boot_started, phase, reporter, sleep)
    reporter.check(
        f"{phase}: SCAN-READY (healthy) reached",
        healthy,
        f"{elapsed:.0f} s from container start (healthcheck performs a real clamdscan)",
    )
    logs = docker.container_logs(plan.clamd_container)
    text = logs.stdout + logs.stderr
    # A cold bootstrap is only proven when BOTH hold: the container reached
    # SCAN-READY *and* the logs show the fresh-clam-database path actually ran.
    # Health alone would let a warm/reused volume (or a silent freshclam
    # failure) pass as a cold bootstrap, so the return value requires both.
    cold_evidence = reporter.check(
        f"{phase}: cold path logged the freshclam bootstrap",
        "no signature database on the volume" in text,
    )
    download = [
        line
        for line in text.splitlines()
        if "Downloading" in line or "MB" in line or "updated" in line.lower()
    ]
    reporter.note(
        f"{phase}: freshclam evidence: "
        + ("; ".join(download[-3:]) if download else "no download lines captured")
    )
    return healthy and cold_evidence


def refresh_created_volumes(docker: DockerCli, plan: IsolationPlan, created: set[str]) -> set[str]:
    """Record volumes now carrying this run's label (preflight proved none existed)."""
    result = docker.volumes_with_label(plan.project)
    if result.returncode == 0:
        created.update(_lines(result))
    return created


def cleanup(docker: DockerCli, plan: IsolationPlan, flags: list[str]) -> CommandResult:
    """Bring down ONLY the isolated project. Volumes are never deleted here."""
    return docker.compose(plan.project, [*flags, "down", "--remove-orphans"], timeout=600)


# ------------------------------------------------------------------------- main
def main(
    argv: list[str] | None = None,
    *,
    docker: DockerCli | None = None,
    workdir: Path | None = None,
    python: str | None = None,
    acceptance_runner: Callable[[str, str, str, str], CommandResult] | None = None,
    http_probe: Callable[[str], tuple[int, str]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    project_name: str | None = None,
    now: datetime.datetime | None = None,
    nonce: str | None = None,
    port_free: Callable[[int], bool] | None = None,
    docker_env: Mapping[str, str] | None = None,
) -> int:
    """Run the isolated acceptance gate. See the module docstring for safety.

    The keyword-only parameters exist for the safety tests (hermetic, mocked
    Docker) and are not CLI flags: ``docker`` (mocked CLI), ``workdir``,
    ``python`` (acceptance interpreter), ``acceptance_runner``, ``http_probe``,
    ``sleep``, ``port_free`` (loopback-port probe), ``docker_env`` (process
    environment for DOCKER_HOST/DOCKER_CONTEXT resolution), and deterministic
    ``project_name``/``now``/``nonce``.
    """
    parser = argparse.ArgumentParser(description="Isolated ClamAV acceptance gate")
    parser.add_argument(
        "--env-file", required=True, help="operator env file (see deploy/.env.prod.example)"
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=8000,
        help="loopback port publishing the isolated api (default 8000)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="also run the second cold bootstrap (deletes THIS RUN's volume)",
    )
    parser.add_argument(
        "--confirm-isolated-test",
        action="store_true",
        help="explicit confirmation required by --full",
    )
    parser.add_argument(
        "--keep-stack",
        action="store_true",
        help="leave the isolated stack up afterwards (for inspection)",
    )
    args = parser.parse_args(argv)
    if args.full and not args.confirm_isolated_test:
        parser.error(
            "--full deletes this run's isolated signature volume; it requires "
            "--confirm-isolated-test (production volumes are never touchable)"
        )

    started_at = datetime.datetime.now(datetime.UTC)
    if project_name is not None and is_reserved_project_name(project_name):
        print(f"[abort] refusing reserved project name: {project_name!r}")
        return 2
    plan = IsolationPlan(
        project=project_name or build_project_name(now=now, nonce=nonce),
        api_port=args.api_port,
        nonce=nonce or secrets.token_hex(4),
    )
    docker = docker or DockerCli()
    http_probe = http_probe or http_get
    acceptance_runner = acceptance_runner or _default_acceptance_runner
    python = python or python_with_httpx()
    workdir = workdir or WORKDIR_ROOT / plan.project

    # The env file is validated and loaded before anything is reported: its
    # values feed the redactor that scrubs every recorded detail, so a value
    # echoed by a failing docker command can never reach the report.
    env_file = Path(args.env_file)
    if not env_file.is_absolute():
        env_file = REPO_ROOT / env_file
    env_problems = validate_env_file(env_file)
    if env_problems:
        for problem in env_problems:
            print(f"[abort] {problem}")
        return 2
    env = load_env(env_file)
    origin = env["ALLOWED_ORIGINS"].split(",")[0].strip()

    def redact(text: str) -> str:
        return redact_env_values(
            text,
            env,
            protect=(
                plan.project,
                plan.clamd_container,
                plan.api_container,
                plan.clamd_volume,
                plan.clamd_image,
                plan.api_image,
                plan.api_url,
            ),
        )

    reporter = Reporter(redactor=redact)
    reporter.lines.append(f"# Isolated ClamAV acceptance — {started_at.isoformat()}")
    reporter.lines.append(f"- isolated Compose project: `{plan.project}`")
    reporter.lines.append(f"- engine container: `{plan.clamd_container}` (this run only)")
    reporter.lines.append(f"- api container: `{plan.api_container}` (this run only)")
    reporter.lines.append(f"- signature volume: `{plan.clamd_volume}` (this run only)")
    reporter.lines.append(f"- api published on: `127.0.0.1:{plan.api_port}` (loopback only)")

    print("== MAFHUM isolated ClamAV acceptance (Phase 2) ==")
    print(f"[isolation] project: {plan.project}")
    print(f"[isolation] this run may only touch: {plan.clamd_container}, {plan.clamd_volume}")

    problems = preflight(docker, plan, port_free=port_free, env=docker_env)
    if problems:
        for problem in problems:
            print(f"[abort] preflight: {problem}")
        print("[abort] nothing was created, stopped or removed.")
        return 2
    reporter.check("preflight: isolated project is unique and unambiguous", True, plan.project)

    created_volumes: set[str] = set()
    wall_start = time.time()
    # The override is a local file (no docker mutation): write it before the
    # try block so `flags` is always bound when the finally block cleans up.
    override = write_override(workdir, plan)
    flags = compose_flags(env_file, override)
    try:
        # Fail-fast sequencing: each phase runs only if every phase it depends
        # on succeeded. A failed config validation, image build or cold
        # bootstrap prevents all dependent phases, and the run still cleans up
        # non-destructively in the finally block below.
        print("\n== phase 0: compose config validation ==")
        cfg = docker.compose(plan.project, [*flags, "config", "-q"])
        config_ok = reporter.check(
            "docker compose config -q passes with the operator env",
            cfg.returncode == 0,
            cfg.stderr.strip()[:200],
        )
        if not config_ok:
            reporter.note(
                "skipping the image build and every dependent phase: compose "
                "config validation failed"
            )

        build_ok = False
        bootstrap_ok = False
        stack_verified = False

        if config_ok:
            print("\n== phase 1: build the clamd image (run-unique tag) ==")
            build_started = time.time()
            build = docker.compose(plan.project, [*flags, "build", "clamd"], timeout=3600)
            if build.returncode == 0:
                detail = f"{time.time() - build_started:.0f} s -> {plan.clamd_image}"
            else:
                detail = build.stderr.strip()[:200]
            build_ok = reporter.check("clamd image built", build.returncode == 0, detail)
            if not build_ok:
                reporter.note(
                    "skipping the cold bootstrap and every dependent phase: the "
                    "clamd image build failed"
                )

        if build_ok:
            print("\n== phase 2: cold bootstrap (fresh isolated volume) ==")
            bootstrap_ok = cold_bootstrap(docker, plan, flags, "phase 2", reporter, sleep)
            if bootstrap_ok:
                refresh_created_volumes(docker, plan, created_volumes)
                reporter.note(f"volumes created by this run: {sorted(created_volumes)}")
            else:
                reporter.note(
                    "skipping db/api start and the acceptance phases: the cold "
                    "bootstrap did not reach SCAN-READY"
                )

        if bootstrap_ok:
            print("\n== phase 3: start db + api on the loopback interface ==")
            up = docker.compose(plan.project, [*flags, "up", "-d", "db", "api"], timeout=900)
            stack_up = up.returncode == 0
            reporter.check("db + api started (isolated project)", stack_up, up.stderr.strip()[:200])
            refresh_created_volumes(docker, plan, created_volumes)
            api_ready = False
            if stack_up:
                # Probe only once this run's `up` succeeded: Docker then owns
                # the 127.0.0.1:<port> binding for this project's api.
                for _ in range(60):
                    status, body = http_probe(plan.api_url + "/health/ready")
                    if status == 200 and '"malware_scanner": "ok"' in body.replace("\n", " "):
                        api_ready = True
                        break
                    sleep(5)
            reporter.check("api /health/ready reports malware_scanner=ok", api_ready)

            # Ownership proof: readiness alone authorises nothing. The api
            # container must carry this run's exact name and project label and
            # must own the 127.0.0.1:<port> host binding, so a foreign local
            # service answering readiness can never pass this gate.
            owned, why = False, "the isolated api did not come up"
            if stack_up and api_ready:
                owned, why = verify_api_ownership(docker, plan)
            reporter.check(
                "api ownership proven: container identity, project label and "
                f"the 127.0.0.1:{plan.api_port} port mapping belong to this run",
                owned,
                why,
            )
            stack_verified = bool(stack_up and api_ready and owned)
            if not stack_verified:
                reporter.note(
                    "refusing to run the acceptance uploads: this run's api is not "
                    f"proven to own {plan.api_url}; nothing was sent to it"
                )

        if stack_verified:
            print("\n== phase 4: three-case acceptance (engine up) ==")
            engine_up = acceptance_runner(python, plan.api_url, origin, "engine-up")
            reporter.check(
                "engine-up acceptance: clean accepted + EICAR rejected (malware_detected)",
                engine_up.returncode == 0,
                engine_up.stderr.strip()[-200:],
            )

            print("\n== phase 5: engine outage fails closed (isolated engine only) ==")
            stop = docker.compose(plan.project, [*flags, "stop", "clamd"], timeout=300)
            stop_ok = reporter.check(
                "this run's clamd container stopped",
                stop.returncode == 0,
                stop.stderr.strip()[:200],
            )
            if stop_ok:
                sleep(5)
                outage = acceptance_runner(python, plan.api_url, origin, "engine-outage")
                reporter.check(
                    "engine-outage acceptance: uploads rejected with scan_* "
                    "(never CLEAN, never malware_detected)",
                    outage.returncode == 0,
                    outage.stderr.strip()[-200:],
                )
            else:
                reporter.note(
                    "skipping the engine-outage upload: this run's clamd container "
                    "could not be stopped, so an upload now would not demonstrate "
                    "fail-closed behavior"
                )

            print("\n== phase 6: recovery reuses the signature volume ==")
            boot_started = time.time()
            start = docker.compose(plan.project, [*flags, "start", "clamd"], timeout=300)
            start_ok = reporter.check(
                "this run's clamd container restarted",
                start.returncode == 0,
                start.stderr.strip()[:200],
            )
            if not start_ok:
                reporter.note(
                    "skipping the recovery evidence, the recovery upload and the "
                    "refresh-loop probe: this run's clamd container did not restart"
                )
            else:
                healthy, elapsed = wait_healthy(
                    docker, plan, boot_started, "phase 6", reporter, sleep
                )
                reporter.check("health returned after restart", healthy, f"{elapsed:.0f} s")
                if not healthy:
                    # A successful `compose start` proves nothing about the
                    # engine's readiness: without SCAN-READY the reuse
                    # evidence is unprovable, the recovery upload could not
                    # demonstrate recovery, and the refresh-loop probe would
                    # inspect a container that is not serving. Skip every
                    # dependent recovery check; the volume is preserved and
                    # only safe cleanup continues.
                    reporter.note(
                        "skipping the recovery evidence, the recovery upload and "
                        "the refresh-loop probe: health did not return to "
                        "SCAN-READY after the restart"
                    )
                else:
                    logs = docker.container_logs(plan.clamd_container)
                    text = logs.stdout + logs.stderr
                    reporter.check(
                        "signature volume REUSED (no fresh bootstrap)",
                        "reusing the signature database on the volume" in text,
                    )
                    recovered = acceptance_runner(python, plan.api_url, origin, "engine-up")
                    reporter.check(
                        "clean upload accepted again after recovery", recovered.returncode == 0
                    )

                    print("\n== phase 7: 12-hour refresh loop is alive ==")
                    probe = docker.exec_refresh_probe(plan.clamd_container)
                    reporter.check(
                        "freshclam refresh loop (sleep 43200) running in this run's container",
                        probe.returncode == 0,
                        "verified via /proc cmdlines; the 12 h cadence itself is verified by "
                        "inspecting `docker logs` after 12 h of uptime",
                    )

        if args.full:
            if not bootstrap_ok:
                reporter.note(
                    "skipping the second cold bootstrap (--full): the first "
                    "bootstrap did not succeed"
                )
            else:
                print("\n== phase 8 (--full): second cold bootstrap (triple-gated) ==")
                reporter.note(
                    "phase 8 will stop this run's own engine, then delete this run's "
                    "own signature volume after verifying creation-by-this-run, "
                    "ownership label and exact name"
                )
                # Stop and remove THIS RUN's container first so the volume is
                # actually removable; nothing outside the isolated project is
                # addressed. Both prerequisites must PROVABLY succeed before
                # the destructive volume deletion is attempted: if either
                # fails, the volume is left in place and the second bootstrap
                # is skipped. Volumes are never deleted as automatic cleanup.
                stop = docker.compose(plan.project, [*flags, "stop", "clamd"], timeout=300)
                stop_ok = reporter.check(
                    "phase 8: this run's clamd container stopped",
                    stop.returncode == 0,
                    stop.stderr.strip()[:200],
                )
                removed_container = False
                if stop_ok:
                    rm = docker.compose(plan.project, [*flags, "rm", "-f", "clamd"], timeout=300)
                    removed_container = reporter.check(
                        "phase 8: this run's clamd container removed",
                        rm.returncode == 0,
                        rm.stderr.strip()[:200],
                    )
                if not (stop_ok and removed_container):
                    reporter.note(
                        "aborting the volume deletion sub-operation: the container "
                        "stop/removal prerequisite failed, so the signature volume is "
                        "left in place (nothing was deleted)"
                    )
                else:
                    removed, why = delete_volume_safely(
                        docker, plan.clamd_volume, plan, created_volumes
                    )
                    if not reporter.check(
                        "volume deletion triple gate passed for this run's volume",
                        removed,
                        why or plan.clamd_volume,
                    ):
                        reporter.note("no volume was deleted; skipping the second bootstrap")
                    else:
                        cold_bootstrap(docker, plan, flags, "phase 8", reporter, sleep)
                        refresh_created_volumes(docker, plan, created_volumes)
    finally:
        if not args.keep_stack:
            print("\n== cleanup: isolated project only, volumes preserved ==")
            down = cleanup(docker, plan, flags)
            reporter.check(
                "isolated stack brought down (no volume deletion)",
                down.returncode == 0,
                down.stderr.strip()[:200],
            )
        else:
            # Finding: the manual cleanup command must reproduce the exact
            # Compose configuration of this run (files, env file, override and
            # project), so the operator can remove the kept stack verbatim.
            manual = " ".join(["docker", "compose", "--project-name", plan.project, *flags, "down"])
            reporter.note(
                "--keep-stack: the isolated stack is still running. Remove it "
                f"with exactly this command: {manual}"
            )
        audit_problems = audit_invocations(docker.invocations, plan)
        reporter.check(
            "self-audit: no command addressed production; every compose command "
            "was scoped to the isolated project",
            not audit_problems,
            "; ".join(audit_problems[:3]),
        )
        elapsed = time.time() - wall_start
        finished = datetime.datetime.now(datetime.UTC).isoformat()
        reporter.lines.append(
            f"**Result: {reporter.passes} passed, {len(reporter.failures)} failed "
            f"({elapsed:.0f} s total, finished {finished}).**"
        )
        if reporter.failures:
            reporter.lines.append("")
            reporter.lines.append("## Failures")
            reporter.lines.extend(f"- {failure}" for failure in reporter.failures)
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            (workdir / "report.md").write_text("\n".join(reporter.lines) + "\n", encoding="utf-8")
        except OSError as exc:
            # The checks have concluded and their output is already printed;
            # an unwritable report is a delivery failure that must surface as
            # a run failure, never as a traceback that masks the result, and
            # never as a silently missing evidence file.
            reason = f"acceptance report could not be written: {exc}"
            reporter.failures.append(reason)
            print(f"[report] {reason}", flush=True)
        else:
            print(f"[report] {workdir / 'report.md'}")
        print(f"\n== RESULT: {reporter.passes} passed, {len(reporter.failures)} failed ==")
        for failure in reporter.failures:
            print(f"  FAIL: {failure}")
        print(f"[isolation] project was: {plan.project}")

    return 0 if not reporter.failures else 1


if __name__ == "__main__":
    sys.exit(main())
