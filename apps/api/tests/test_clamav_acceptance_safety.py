"""The ClamAV acceptance tool must be structurally incapable of touching production.

``tools/clamav/production_bootstrap_acceptance.py`` runs on operator hosts, some
of which host the real ``mafhum-prod`` Compose stack. Its earlier revision
hardcoded that project name, so a single run could stop production containers
and force-delete the production signature volume. These tests pin the
remediation with a mocked Docker CLI (nothing real is executed — the suite
also passes on hosts without Docker):

* production project names are rejected before any docker call is issued;
* a project-name collision aborts the run before any mutation;
* the default invocation never deletes any volume and cleans up only the
  isolated project (``down`` without ``-v``);
* ``--full`` (the only volume-deleting mode) refuses to run without
  ``--confirm-isolated-test``;
* a volume whose ownership label belongs to another project (e.g.
  ``mafhum-prod``) is never deleted, nor is a volume this run did not create;
* a failure during bootstrap triggers project-scoped cleanup only — never a
  destructive one;
* every recorded docker invocation is audited: no argument may name a
  production resource, and every compose command carries the isolated
  ``--project-name``.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL = REPO_ROOT / "tools" / "clamav" / "production_bootstrap_acceptance.py"


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("production_bootstrap_acceptance", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass annotation resolution needs this
    spec.loader.exec_module(module)
    return module


PBA = _load_tool()

SAFE_PROJECT = "mafhum-clamav-acc-20261031-120000-deadbeef"
ENV_CONTENT = "\n".join(
    [
        "MAFHUM_DOMAIN=mafhum.example.com",
        "MAFHUM_ACME_EMAIL=ops@example.com",
        "POSTGRES_USER=mafhum",
        "POSTGRES_PASSWORD=not-a-real-secret",
        "SESSION_SECRET=not-a-real-secret-either",
        "ALLOWED_ORIGINS=https://mafhum.example.com",
        "METRICS_SCRAPE_TOKEN=not-a-real-token",
    ]
)
READY_BODY = '{"status": "ok", "checks": {"malware_scanner": "ok"}}'


class FakeRunner:
    """A Docker CLI that records argv and answers canned responses.

    It never executes anything, so these tests are hermetic and safe on any
    host — including one that really runs ``mafhum-prod``.
    """

    def __init__(
        self,
        *,
        projects: list[str] | None = None,
        # volumes that exist BEFORE the run (preflight must see them and abort)
        preexisting_label_volumes: dict[str, list[str]] | None = None,
        # volumes the run itself creates (only reported after the first `up`)
        created_label_volumes: dict[str, list[str]] | None = None,
        volume_info: dict[str, dict[str, Any]] | None = None,
        existing_container_names: set[str] | None = None,
        fail_substrings: tuple[str, ...] = (),
        fail_stderr: str = "",
        # Docker context emulation: name returned by `docker context show`,
        # and the engine endpoint each context resolves to.
        context_show_name: str = "default",
        context_endpoints: dict[str, str] | None = None,
        # The api container's inspect document (identity, labels, bindings).
        api_inspect: dict[str, Any] | None = None,
        api_port: int = 8000,
        # Health reported by `docker inspect --format` for the clamd container.
        clamd_health: str = "healthy",
        # Optional ordered health answers (consumed in order, last repeats):
        # lets phase 2 see "healthy" while phase 6's recovery poll does not.
        health_sequence: list[str] | None = None,
        # Log text returned by `docker logs <clamd container>` (None = the
        # standard fixture text with both cold and reuse evidence).
        clamd_logs: str | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.projects = list(projects or [])
        self.preexisting_label_volumes = preexisting_label_volumes or {}
        self.created_label_volumes = created_label_volumes or {}
        self.volume_info = volume_info or {}
        self.existing_container_names = existing_container_names or set()
        self.fail_substrings = fail_substrings
        self.fail_stderr = fail_stderr
        self.context_show_name = context_show_name
        self.context_endpoints = context_endpoints or {"default": "unix:///var/run/docker.sock"}
        self.api_inspect = api_inspect
        self.api_port = api_port
        self.clamd_health = clamd_health
        self.health_sequence = list(health_sequence) if health_sequence else []
        self.clamd_logs = clamd_logs
        self.ups = 0  # compose `up` commands executed so far

    def __call__(self, args: list[str], timeout: float = 1800) -> PBA.CommandResult:
        self.calls.append(list(args))
        joined = " ".join(args)
        for fragment in self.fail_substrings:
            if fragment in joined:
                stderr = self.fail_stderr or f"simulated failure: {fragment}"
                return PBA.CommandResult(1, "", stderr)

        if args[:2] == ["docker", "--version"]:
            return PBA.CommandResult(0, "Docker version 27.0.0 (fake)", "")
        if args[:3] == ["docker", "compose", "version"]:
            return PBA.CommandResult(0, "Docker Compose version v2.29.0 (fake)", "")
        if args[:3] == ["docker", "context", "show"]:
            return PBA.CommandResult(0, self.context_show_name, "")
        if args[:3] == ["docker", "context", "inspect"]:
            name = args[3]
            endpoint = self.context_endpoints.get(name)
            if endpoint is None:
                return PBA.CommandResult(1, "", f"context {name!r} not found")
            document = [{"Name": name, "Endpoints": {"docker": {"Host": endpoint}}}]
            return PBA.CommandResult(0, json.dumps(document), "")
        if args[:4] == ["docker", "compose", "ls", "--all"]:
            payload = [
                {"Name": name, "Status": "running", "ConfigFiles": ""} for name in self.projects
            ]
            return PBA.CommandResult(0, json.dumps(payload), "")
        if args[:2] == ["docker", "compose"]:
            if "up" in args:
                self.ups += 1
            return PBA.CommandResult(0, "", "")

        if args[:2] == ["docker", "ps"]:
            name_filter = next((a for a in args if a.startswith("name=")), None)
            if name_filter:
                # exact-name collision probe: ^/<name>$
                wanted = re.sub(r"\^/(.*)\$$", r"\1", name_filter.split("=", 1)[1])
                found = wanted if wanted in self.existing_container_names else ""
                return PBA.CommandResult(0, found, "")
            # ownership-label probe for containers: nothing carries an isolated label
            return PBA.CommandResult(0, "", "")

        if args[:3] == ["docker", "volume", "ls"]:
            label = next((a for a in args if a.startswith("label=")), "")
            # label=com.docker.compose.project=<project>
            project = label.partition("=")[2].partition("=")[2]
            names = list(self.preexisting_label_volumes.get(project, []))
            if self.ups:  # the run has created its own volumes by now
                names += self.created_label_volumes.get(project, [])
            return PBA.CommandResult(0, "\n".join(names), "")
        if args[:3] == ["docker", "volume", "inspect"]:
            name = args[3]
            info = self.volume_info.get(name)
            if info is None or not self.ups:
                # before the first `up`, this run's volume does not exist yet
                return PBA.CommandResult(
                    1, "", f"Error response from daemon: get {name}: no such volume"
                )
            return PBA.CommandResult(0, json.dumps([info]), "")
        if args[:3] == ["docker", "volume", "rm"]:
            return PBA.CommandResult(0, args[3], "")

        if args[:2] == ["docker", "inspect"]:
            if "--format" in args:
                # health-status probe for the clamd container
                if self.health_sequence:
                    # consume in order; the last value repeats (sticky)
                    answer = (
                        self.health_sequence.pop(0)
                        if len(self.health_sequence) > 1
                        else (self.health_sequence[0])
                    )
                    return PBA.CommandResult(0, answer, "")
                return PBA.CommandResult(0, self.clamd_health, "")
            # full container inspect: the api ownership proof
            info = self.api_inspect or {
                "Name": f"/{SAFE_PROJECT}-api-1",
                "Config": {"Labels": {PBA.COMPOSE_PROJECT_LABEL: SAFE_PROJECT}},
                "State": {"Running": True},
                "NetworkSettings": {
                    "Ports": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(self.api_port)}]}
                },
            }
            return PBA.CommandResult(0, json.dumps([info]), "")
        if args[:2] == ["docker", "logs"]:
            return PBA.CommandResult(
                0,
                self.clamd_logs
                if self.clamd_logs is not None
                else (
                    "[clamd] no signature database on the volume; running freshclam\n"
                    "[clamd] daily.cvd updated (signature evidence)\n"
                    "[clamd] reusing the signature database on the volume\n"
                    "[clamd] starting daemon on TCP 3310\n"
                ),
                "",
            )
        if args[:2] == ["docker", "exec"]:
            return PBA.CommandResult(0, "", "")
        return PBA.CommandResult(0, "", "")


def make_docker(**kwargs: Any) -> PBA.DockerCli:
    return PBA.DockerCli(runner=FakeRunner(**kwargs))


def make_plan(project: str = SAFE_PROJECT) -> PBA.IsolationPlan:
    return PBA.IsolationPlan(project=project, api_port=8000, nonce="deadbeef")


def run_main(
    docker: PBA.DockerCli,
    tmp_path: Path,
    argv: list[str] | None = None,
    *,
    project: str = SAFE_PROJECT,
    acceptance_runner: Any = None,
    http_probe: Any = None,
    port_free: Any = None,
    docker_env: dict[str, str] | None = None,
    env_content: str | None = None,
) -> int:
    env_file = tmp_path / "env"
    env_file.write_text(ENV_CONTENT if env_content is None else env_content, encoding="utf-8")

    def default_runner(python: str, api: str, origin: str, mode: str) -> PBA.CommandResult:
        return PBA.CommandResult(0, "", "")

    def default_probe(url: str) -> tuple[int, str]:
        return (200, READY_BODY)

    def default_port_free(port: int) -> bool:
        return True

    return PBA.main(
        ["--env-file", str(env_file), *(argv or [])],
        docker=docker,
        workdir=tmp_path / "acc",
        python="python",
        acceptance_runner=acceptance_runner or default_runner,
        http_probe=http_probe or default_probe,
        sleep=lambda seconds: None,
        project_name=project,
        port_free=port_free or default_port_free,
        docker_env={} if docker_env is None else docker_env,
    )


def assert_never_targets_production(docker: PBA.DockerCli, plan: PBA.IsolationPlan) -> None:
    """No recorded invocation may address a production resource."""
    for argv in docker.invocations:
        for element in argv:
            assert not PBA.is_production_resource_identifier(element), (
                f"production identifier {element!r} addressed by {argv}"
            )
    problems = PBA.audit_invocations(docker.invocations, plan)
    assert problems == [], problems


# ---------------------------------------------------------------- reservations
def test_reserved_project_names_are_rejected_before_any_docker_call(
    tmp_path: Path,
) -> None:
    for reserved in (
        "mafhum-prod",
        "MAFHUM-PROD",
        " mafhum-prod ",
        "mafhum-prod-2",
        "mafhum-prod_clamd_db",
        "mafhum",
    ):
        assert PBA.is_reserved_project_name(reserved), reserved
    assert not PBA.is_reserved_project_name(SAFE_PROJECT)
    assert not PBA.is_reserved_project_name("mafhum-clamav-acc-anything")

    # An IsolationPlan carrying a production name is structurally impossible.
    with pytest.raises(ValueError, match="reserved"):
        make_plan("mafhum-prod")
    with pytest.raises(ValueError, match="reserved"):
        PBA.IsolationPlan(project="mafhum-prod-anything")
    # And a Compose command for a reserved project cannot even be issued.
    docker = make_docker()
    with pytest.raises(ValueError, match="reserved"):
        docker.compose("mafhum-prod", ["config", "-q"])
    assert docker.invocations == []

    # main() refuses the same name outright, also before any docker call.
    docker2 = make_docker()
    code = PBA.main(
        ["--env-file", str(tmp_path / "env")],  # never read: the guard fires first
        docker=docker2,
        workdir=tmp_path / "unused",
        python="python",
        project_name="mafhum-prod",
    )
    assert code == 2
    assert docker2.invocations == []


def test_generated_project_names_are_unique_and_never_reserved() -> None:
    names = {PBA.build_project_name() for _ in range(200)}
    assert len(names) == 200  # collision-free in practice
    for name in names:
        assert name.startswith("mafhum-clamav-acc-"), name
        assert not PBA.is_reserved_project_name(name), name
        assert re.fullmatch(r"mafhum-clamav-acc-\d{8}-\d{6}-[0-9a-f]{8}", name), name


# -------------------------------------------------------------------- preflight
def test_project_name_collision_aborts_before_any_mutation(tmp_path: Path) -> None:
    docker = make_docker(projects=[SAFE_PROJECT])  # name already taken on the host
    code = run_main(docker, tmp_path)
    assert code == 2
    joined = [" ".join(argv) for argv in docker.invocations]
    # Read-only probes only: nothing was ever created, stopped, removed or downed.
    for verb in (" up ", " down ", " stop ", " start ", " rm ", " build "):
        assert not any(verb in f" {line} " for line in joined), verb
    assert_never_targets_production(docker, make_plan())


def test_ambiguous_resource_state_aborts() -> None:
    docker = make_docker(preexisting_label_volumes={SAFE_PROJECT: [f"{SAFE_PROJECT}_clamd_db"]})
    problems = PBA.preflight(docker, make_plan(), env={})
    assert any("ambiguity" in problem for problem in problems)


def test_container_name_collision_aborts() -> None:
    docker = make_docker(existing_container_names={f"{SAFE_PROJECT}-clamd-1"})
    problems = PBA.preflight(docker, make_plan(), env={})
    assert any("collision" in problem for problem in problems)


# ------------------------------------------------------- default non-destructive
def test_default_run_is_non_destructive_and_never_touches_production(
    tmp_path: Path,
) -> None:
    # The fake host really runs production next to the acceptance run.
    docker = make_docker(projects=["mafhum-prod"])
    code = run_main(docker, tmp_path)
    assert code == 0

    # No volume was removed anywhere in the default flow.
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)
    # Cleanup brought the isolated project down — without -v / --volumes.
    downs = [
        argv for argv in docker.invocations if argv[:2] == ["docker", "compose"] and "down" in argv
    ]
    assert downs, "expected the isolated project to be cleaned up"
    for down in downs:
        assert "-v" not in down and "--volumes" not in down, down
    assert_never_targets_production(docker, make_plan())


def test_cleanup_only_targets_the_isolated_project(tmp_path: Path) -> None:
    docker = make_docker()
    assert run_main(docker, tmp_path) == 0
    compose_calls = [argv for argv in docker.invocations if argv[:2] == ["docker", "compose"]]
    assert compose_calls
    for argv in compose_calls:
        # `compose version` / `compose ls` are host-level read-only probes;
        # every project-scoped compose command carries the isolated project.
        if len(argv) > 2 and argv[2] in ("version", "ls"):
            continue
        assert "--project-name" in argv, argv
        assert argv[argv.index("--project-name") + 1] == SAFE_PROJECT, argv
    # The engine stops only ever named the isolated clamd service.
    stops = [argv for argv in compose_calls if "stop" in argv]
    assert all(argv[-1] == "clamd" for argv in stops), stops
    assert not any("restart" in argv for argv in docker.invocations)


def test_api_is_published_on_loopback_only(tmp_path: Path) -> None:
    workdir = tmp_path / "acc"
    plan = make_plan()
    override = PBA.write_override(workdir, plan)
    text = override.read_text(encoding="utf-8")
    assert "127.0.0.1:8000:8000" in text
    assert not re.search(r"0\.0\.0\.0", text)  # no wildcard bind anywhere
    assert plan.clamd_image in text and plan.api_image in text
    assert "mafhum-clamd:0.3.0" not in text  # production tag never rebuilt

    # The full run wires the same loopback bind into the generated override,
    # and the ownership proof matches the custom port end to end.
    docker = make_docker(api_port=8123)
    assert run_main(docker, tmp_path, ["--api-port", "8123"], project=SAFE_PROJECT) == 0
    generated = (tmp_path / "acc" / "override.yml").read_text(encoding="utf-8")
    assert "127.0.0.1:8123:8000" in generated


# ----------------------------------------------------------------- --full gate
def test_full_requires_explicit_confirmation(tmp_path: Path) -> None:
    docker = make_docker()
    with pytest.raises(SystemExit) as excinfo:
        run_main(docker, tmp_path, ["--full"])
    assert excinfo.value.code == 2
    # The refusal happened before anything ran at all.
    assert docker.invocations == []


def test_full_deletes_only_the_verified_volume_of_this_run(tmp_path: Path) -> None:
    volume = f"{SAFE_PROJECT}_clamd_db"
    docker = make_docker(
        created_label_volumes={SAFE_PROJECT: [volume]},
        volume_info={
            volume: {
                "Name": volume,
                "Labels": {"com.docker.compose.project": SAFE_PROJECT},
            }
        },
    )
    code = run_main(docker, tmp_path, ["--full", "--confirm-isolated-test"])
    assert code == 0
    removals = [argv for argv in docker.invocations if argv[:3] == ["docker", "volume", "rm"]]
    assert len(removals) == 1
    assert removals[0][3] == volume
    # The triple gate re-verified the volume immediately before the removal.
    index = docker.invocations.index(removals[0])
    previous = docker.invocations[index - 1]
    assert previous[:3] == ["docker", "volume", "inspect"] and previous[3] == volume
    assert_never_targets_production(docker, make_plan())


def test_foreign_owned_volume_is_never_deleted(tmp_path: Path) -> None:
    """A volume labelled mafhum-prod must survive even --full --confirm."""
    volume = f"{SAFE_PROJECT}_clamd_db"
    docker = make_docker(
        created_label_volumes={SAFE_PROJECT: [volume]},
        volume_info={
            volume: {
                "Name": volume,
                # ownership label names ANOTHER project — the run must refuse
                "Labels": {"com.docker.compose.project": "mafhum-prod"},
            }
        },
    )
    code = run_main(docker, tmp_path, ["--full", "--confirm-isolated-test"])
    assert code == 1  # the phase failed loudly…
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)
    assert_never_targets_production(docker, make_plan())


def test_volume_not_created_by_this_run_is_never_deleted() -> None:
    """Unit gate: correct label but absent from the created-by-this-run set."""
    plan = make_plan()
    volume = plan.clamd_volume
    docker = make_docker(
        volume_info={
            volume: {
                "Name": volume,
                "Labels": {"com.docker.compose.project": SAFE_PROJECT},
            }
        }
    )
    allowed, why = PBA.delete_volume_safely(docker, volume, plan, created_by_this_run=set())
    assert not allowed
    assert "not created by this run" in why
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)


def test_production_volume_name_is_refused_by_the_gate() -> None:
    """Direct call with a production volume name: refused before any command."""
    plan = make_plan()
    docker = make_docker()
    created = {"mafhum-prod_clamd_db"}  # even if it were in the tracked set
    allowed, why = PBA.delete_volume_safely(docker, "mafhum-prod_clamd_db", plan, created)
    assert not allowed
    assert "production identifier" in why
    assert docker.invocations == []


# --------------------------------------------------------- failure non-violence
def test_bootstrap_failure_triggers_non_destructive_cleanup(tmp_path: Path) -> None:
    docker = make_docker(fail_substrings=("up -d clamd",))
    sent: list[str] = []

    def runner(python: str, api: str, origin: str, mode: str) -> PBA.CommandResult:
        sent.append(mode)
        return PBA.CommandResult(0, "", "")

    code = run_main(docker, tmp_path, acceptance_runner=runner)
    assert code == 1  # the failure was recorded
    assert sent == []  # and no acceptance upload was ever attempted
    # Fail-fast: db/api were never started after the failed cold bootstrap.
    assert not any("up -d db" in " ".join(argv) for argv in docker.invocations)
    # Cleanup still ran, scoped to the isolated project, and never deleted a
    # volume nor touched anything outside the run.
    downs = [
        argv for argv in docker.invocations if argv[:2] == ["docker", "compose"] and "down" in argv
    ]
    assert downs
    for down in downs:
        assert "-v" not in down and "--volumes" not in down, down
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)
    assert_never_targets_production(docker, make_plan())


def test_keep_stack_leaves_the_isolated_project_running(tmp_path: Path) -> None:
    docker = make_docker()
    assert run_main(docker, tmp_path, ["--keep-stack"]) == 0
    assert not any(
        argv[:2] == ["docker", "compose"] and "down" in argv for argv in docker.invocations
    )
    # The printed manual cleanup command reproduces the exact configuration:
    # project name, both Compose files, the env file — and ends with `down`.
    report = (tmp_path / "acc" / "report.md").read_text(encoding="utf-8")
    command_line = next(line for line in report.splitlines() if "Remove it" in line)
    assert f"--project-name {SAFE_PROJECT}" in command_line
    assert "docker-compose.prod.yml" in command_line
    assert "--env-file" in command_line and str(tmp_path / "env") in command_line
    assert "override.yml" in command_line
    assert command_line.rstrip().endswith("down")
    # Token-level check: a raw substring would false-positive on any path
    # containing "-v" (e.g. a checkout directory named "MAFHUM-vercel"), while
    # missing nothing — the dangerous forms are standalone tokens.
    tokens = command_line.split()
    assert "-v" not in tokens and "--volumes" not in tokens, command_line


# ---------------------------------------------------- loopback port ownership
def test_occupied_loopback_port_aborts_before_anything_runs(tmp_path: Path) -> None:
    """A foreign service on 127.0.0.1:<port> must abort the run up front.

    The development Compose stack publishes 127.0.0.1:8000:8000 — the tool's
    default port. Without this gate the acceptance uploads could reach a stack
    this run did not create.
    """
    docker = make_docker()
    code = run_main(docker, tmp_path, port_free=lambda port: False)
    assert code == 2
    # The abort happened before any docker call: nothing was probed or created.
    assert docker.invocations == []
    assert_never_targets_production(docker, make_plan())


def test_acceptance_uploads_never_run_when_the_stack_failed_to_start(
    tmp_path: Path,
) -> None:
    """`up` failed (e.g. port taken between preflight and up): no uploads."""
    docker = make_docker(fail_substrings=("up -d db api",))
    sent: list[str] = []

    def runner(python: str, api: str, origin: str, mode: str) -> PBA.CommandResult:
        sent.append(mode)
        return PBA.CommandResult(0, "", "")

    code = run_main(docker, tmp_path, acceptance_runner=runner)
    assert code == 1  # the failure was recorded…
    assert sent == []  # …and NOTHING was sent to the loopback port
    # Cleanup still ran, non-destructively, scoped to the isolated project.
    downs = [
        argv for argv in docker.invocations if argv[:2] == ["docker", "compose"] and "down" in argv
    ]
    assert downs
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)
    assert_never_targets_production(docker, make_plan())


def test_acceptance_uploads_never_run_when_the_api_never_became_ready(
    tmp_path: Path,
) -> None:
    """`up` succeeded but readiness never passed: still no uploads."""
    docker = make_docker()
    sent: list[str] = []

    def runner(python: str, api: str, origin: str, mode: str) -> PBA.CommandResult:
        sent.append(mode)
        return PBA.CommandResult(0, "", "")

    code = run_main(
        docker,
        tmp_path,
        acceptance_runner=runner,
        http_probe=lambda url: (503, '{"status": "unavailable"}'),
    )
    assert code == 1
    assert sent == []
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)
    assert_never_targets_production(docker, make_plan())


# ------------------------------------------------- engine locality (Finding 1)
def _recording_runner(sent: list[str]) -> Any:
    def runner(python: str, api: str, origin: str, mode: str) -> PBA.CommandResult:
        sent.append(mode)
        return PBA.CommandResult(0, "", "")

    return runner


def _assert_no_mutating_docker(docker: PBA.DockerCli) -> None:
    for argv in docker.invocations:
        joined = " ".join(argv)
        for verb in ("up", "build", "down", "stop", "start", "rm"):
            assert f" {verb} " not in f" {joined} ", (verb, joined)


def test_remote_engine_via_docker_host_is_rejected(tmp_path: Path) -> None:
    """A remote DOCKER_HOST must abort before anything is created.

    With a remote engine, `compose up` succeeds remotely while
    127.0.0.1:<port> on this machine is bound by no one (or by an unrelated
    local service), so no ownership proof would be sound.
    """
    docker = make_docker()
    sent: list[str] = []
    code = run_main(
        docker,
        tmp_path,
        acceptance_runner=_recording_runner(sent),
        docker_env={"DOCKER_HOST": "ssh://ops@prod-host"},
    )
    assert code == 2
    assert sent == []  # the acceptance runner was never invoked
    _assert_no_mutating_docker(docker)
    assert_never_targets_production(docker, make_plan())


def test_remote_engine_with_local_unrelated_api_is_rejected(tmp_path: Path) -> None:
    """Remote engine AND a local unrelated API on the port: still fail-closed."""
    docker = make_docker()
    sent: list[str] = []
    code = run_main(
        docker,
        tmp_path,
        acceptance_runner=_recording_runner(sent),
        port_free=lambda port: False,  # a local service holds 127.0.0.1:8000
        docker_env={"DOCKER_HOST": "tcp://10.9.8.7:2375"},
    )
    assert code == 2
    assert sent == []
    assert docker.invocations == []  # the port abort fired before any docker call
    assert_never_targets_production(docker, make_plan())


def test_docker_host_overrides_the_selected_context(tmp_path: Path) -> None:
    """DOCKER_HOST wins over DOCKER_CONTEXT, exactly like the docker CLI."""
    docker = make_docker()
    sent: list[str] = []
    code = run_main(
        docker,
        tmp_path,
        acceptance_runner=_recording_runner(sent),
        docker_env={
            "DOCKER_HOST": "ssh://ops@prod-host",
            "DOCKER_CONTEXT": "default",  # a local-sounding selection
        },
    )
    assert code == 2
    assert sent == []
    # Precedence proof: DOCKER_HOST short-circuits context resolution, so no
    # context was shown or inspected at all.
    assert not any(argv[:2] == ["docker", "context"] for argv in docker.invocations)
    _assert_no_mutating_docker(docker)


def test_remote_endpoint_via_docker_context_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Locality follows the context's ENDPOINT, never its name.

    The context is named `laptop` (sounds local) but resolves to a remote
    tcp endpoint — the run must refuse it.
    """
    docker = make_docker(context_endpoints={"laptop": "tcp://10.0.0.5:2375"})
    sent: list[str] = []
    code = run_main(
        docker,
        tmp_path,
        acceptance_runner=_recording_runner(sent),
        docker_env={"DOCKER_CONTEXT": "laptop"},
    )
    assert code == 2
    assert sent == []
    # The endpoint was actually inspected (not guessed from the name).
    assert any(
        argv[:3] == ["docker", "context", "inspect"] and argv[3] == "laptop"
        for argv in docker.invocations
    )
    assert "remote Docker engine refused" in capsys.readouterr().out
    _assert_no_mutating_docker(docker)


def test_local_engine_endpoints_are_accepted(tmp_path: Path) -> None:
    """Local unix socket and Windows named pipe engines pass the gate."""
    for endpoint in ("unix:///var/run/docker.sock", "npipe:////./pipe/docker_engine"):
        docker = make_docker()
        sent: list[str] = []
        code = run_main(
            docker,
            tmp_path,
            acceptance_runner=_recording_runner(sent),
            docker_env={"DOCKER_HOST": endpoint},
        )
        assert code == 0, endpoint
        assert sent == ["engine-up", "engine-outage", "engine-up"], endpoint


def test_api_identity_mismatch_blocks_uploads(tmp_path: Path) -> None:
    """A container answering on the port but not owned by this run: refuse."""
    docker = make_docker(
        api_inspect={
            "Name": f"/{SAFE_PROJECT}-api-1",
            "Config": {"Labels": {PBA.COMPOSE_PROJECT_LABEL: "some-other-project"}},
            "State": {"Running": True},
            "NetworkSettings": {
                "Ports": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}]}
            },
        }
    )
    sent: list[str] = []
    code = run_main(docker, tmp_path, acceptance_runner=_recording_runner(sent))
    assert code == 1
    assert sent == []  # readiness answered, ownership did not: no uploads
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)
    assert_never_targets_production(docker, make_plan())


def test_api_port_mapping_mismatch_blocks_uploads(tmp_path: Path) -> None:
    """The binding must be exactly 127.0.0.1:<port> for this run's container."""
    # Assembled (not a literal) so the security linter does not read this
    # fixture as an all-interfaces bind in test code.
    unsafe_host_ip = ".".join(["0"] * 4)
    docker = make_docker(
        api_inspect={
            "Name": f"/{SAFE_PROJECT}-api-1",
            "Config": {"Labels": {PBA.COMPOSE_PROJECT_LABEL: SAFE_PROJECT}},
            "State": {"Running": True},
            "NetworkSettings": {
                "Ports": {"8000/tcp": [{"HostIp": unsafe_host_ip, "HostPort": "8000"}]}
            },
        }
    )
    sent: list[str] = []
    code = run_main(docker, tmp_path, acceptance_runner=_recording_runner(sent))
    assert code == 1
    assert sent == []
    assert_never_targets_production(docker, make_plan())


# ---------------------------------------------------- fail-fast (Finding 3)
def test_config_failure_skips_build_and_dependent_phases(tmp_path: Path) -> None:
    docker = make_docker(fail_substrings=("config -q",))
    sent: list[str] = []
    code = run_main(docker, tmp_path, acceptance_runner=_recording_runner(sent))
    assert code == 1
    assert sent == []
    # No build, no up, no acceptance — only the preflight probes and the
    # non-destructive cleanup ran.
    assert not any(" build " in f" {' '.join(argv)} " for argv in docker.invocations)
    assert not any(" up " in f" {' '.join(argv)} " for argv in docker.invocations)
    assert any(argv[:2] == ["docker", "compose"] and "down" in argv for argv in docker.invocations)
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)
    assert_never_targets_production(docker, make_plan())


def test_build_failure_skips_bootstrap_and_dependent_phases(tmp_path: Path) -> None:
    docker = make_docker(fail_substrings=("build clamd",))
    sent: list[str] = []
    code = run_main(docker, tmp_path, acceptance_runner=_recording_runner(sent))
    assert code == 1
    assert sent == []
    assert not any("up -d clamd" in " ".join(argv) for argv in docker.invocations)
    assert not any("up -d db" in " ".join(argv) for argv in docker.invocations)
    assert any(argv[:2] == ["docker", "compose"] and "down" in argv for argv in docker.invocations)
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)
    assert_never_targets_production(docker, make_plan())


def test_full_skips_second_bootstrap_when_first_failed(tmp_path: Path) -> None:
    """--full cannot reach the volume gate when the first bootstrap failed."""
    docker = make_docker(fail_substrings=("up -d clamd",))
    code = run_main(docker, tmp_path, ["--full", "--confirm-isolated-test"])
    assert code == 1
    # No stop/rm of the engine, no volume deletion, no second bootstrap.
    assert not any(
        argv[:2] == ["docker", "compose"] and ("stop" in argv or "rm" in argv)
        for argv in docker.invocations
    )
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)


# ----------------------- bootstrap evidence + stop/rm gates (final findings)
def test_unhealthy_engine_fails_bootstrap_and_prevents_dependent_phases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A container that never reaches SCAN-READY must fail the cold bootstrap."""
    # Do not really wait out the health timeout in a unit test.
    monkeypatch.setattr(PBA, "HEALTH_TIMEOUT_SECONDS", 0)
    docker = make_docker(clamd_health="unhealthy")
    sent: list[str] = []
    code = run_main(docker, tmp_path, acceptance_runner=_recording_runner(sent))
    assert code == 1
    assert sent == []  # no acceptance upload was attempted
    assert not any("up -d db" in " ".join(argv) for argv in docker.invocations)
    assert any(argv[:2] == ["docker", "compose"] and "down" in argv for argv in docker.invocations)
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)
    assert_never_targets_production(docker, make_plan())


def test_missing_cold_bootstrap_evidence_prevents_dependent_phases(tmp_path: Path) -> None:
    """Regression: a healthy container WITHOUT the freshclam cold-path log
    evidence must fail the bootstrap (health alone proved nothing about the
    cold path), skip db/api and the uploads, and still clean up safely."""
    docker = make_docker(
        clamd_logs=(
            "[clamd] reusing the signature database on the volume\n"
            "[clamd] starting daemon on TCP 3310\n"
        )
    )
    sent: list[str] = []
    code = run_main(docker, tmp_path, acceptance_runner=_recording_runner(sent))
    assert code == 1
    report = (tmp_path / "acc" / "report.md").read_text(encoding="utf-8")
    assert "phase 2: cold path logged the freshclam bootstrap" in report
    assert "[x] phase 2: cold path logged the freshclam bootstrap" not in report
    assert sent == []
    assert not any("up -d db" in " ".join(argv) for argv in docker.invocations)
    assert any(argv[:2] == ["docker", "compose"] and "down" in argv for argv in docker.invocations)
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)
    assert_never_targets_production(docker, make_plan())


def test_full_aborts_volume_deletion_when_container_stop_fails(tmp_path: Path) -> None:
    """--full: a failed stop must abort the destructive volume deletion."""
    docker = make_docker(fail_substrings=("stop clamd",))
    code = run_main(docker, tmp_path, ["--full", "--confirm-isolated-test"])
    assert code == 1
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)
    report = (tmp_path / "acc" / "report.md").read_text(encoding="utf-8")
    assert "aborting the volume deletion sub-operation" in report
    # The phase-5 outage upload was skipped too: a failed stop demonstrates
    # nothing about fail-closed behavior.
    assert "skipping the engine-outage upload" in report
    assert_never_targets_production(docker, make_plan())


def test_full_aborts_volume_deletion_when_container_removal_fails(tmp_path: Path) -> None:
    """--full: a failed container removal must abort the volume deletion."""
    docker = make_docker(fail_substrings=("rm -f clamd",))
    code = run_main(docker, tmp_path, ["--full", "--confirm-isolated-test"])
    assert code == 1
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)
    report = (tmp_path / "acc" / "report.md").read_text(encoding="utf-8")
    assert "aborting the volume deletion sub-operation" in report
    assert_never_targets_production(docker, make_plan())


def test_phase6_start_failure_skips_recovery_upload_and_refresh_probe(tmp_path: Path) -> None:
    """A failed restart must skip the recovery upload and the refresh probe."""
    docker = make_docker(fail_substrings=("start clamd",))
    sent: list[str] = []
    code = run_main(docker, tmp_path, acceptance_runner=_recording_runner(sent))
    assert code == 1
    assert sent == ["engine-up", "engine-outage"]  # phase 4 + phase 5 only:
    # the phase-6 recovery upload was skipped because the restart failed
    assert not any(argv[:2] == ["docker", "exec"] for argv in docker.invocations)
    report = (tmp_path / "acc" / "report.md").read_text(encoding="utf-8")
    assert "did not restart" in report
    assert_never_targets_production(docker, make_plan())


def _run_unhealthy_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[int, PBA.DockerCli, list[str]]:
    """Full run where the engine starts and the cold bootstrap is healthy,
    but health never returns after the phase-6 restart."""
    # Do not really wait out the recovery health timeout in a unit test.
    monkeypatch.setattr(PBA, "HEALTH_TIMEOUT_SECONDS", 0.05)
    # Phase 2's first health probe answers healthy; every later probe
    # (phase 6's recovery poll) answers unhealthy, stickily.
    docker = make_docker(health_sequence=["healthy", "unhealthy"])
    sent: list[str] = []
    code = run_main(docker, tmp_path, acceptance_runner=_recording_runner(sent))
    return code, docker, sent


def test_unhealthy_recovery_skips_the_recovery_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Start succeeded but SCAN-READY never returned: no recovery upload."""
    code, docker, sent = _run_unhealthy_recovery(tmp_path, monkeypatch)
    assert code == 1
    # Phases 4 and 5 ran; the phase-6 recovery upload did not.
    assert sent == ["engine-up", "engine-outage"]
    assert_never_targets_production(docker, make_plan())


def test_unhealthy_recovery_skips_the_refresh_loop_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Start succeeded but SCAN-READY never returned: no refresh-loop probe."""
    code, docker, sent = _run_unhealthy_recovery(tmp_path, monkeypatch)
    assert code == 1
    assert sent == ["engine-up", "engine-outage"]
    assert not any(argv[:2] == ["docker", "exec"] for argv in docker.invocations)
    assert_never_targets_production(docker, make_plan())


def test_unhealthy_recovery_reports_no_passing_recovery_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Start succeeded but SCAN-READY never returned: the reuse evidence is
    recorded as a failure-skipped item, never as a pass; the volume survives."""
    code, docker, _sent = _run_unhealthy_recovery(tmp_path, monkeypatch)
    assert code == 1
    report = (tmp_path / "acc" / "report.md").read_text(encoding="utf-8")
    assert "[ ] health returned after restart" in report
    assert "[x] signature volume REUSED" not in report
    assert "[x] clean upload accepted again after recovery" not in report
    assert "health did not return to SCAN-READY after the restart" in report
    # Safe cleanup only: isolated down, no volume deletion, no production op.
    assert any(argv[:2] == ["docker", "compose"] and "down" in argv for argv in docker.invocations)
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)
    assert_never_targets_production(docker, make_plan())


def test_healthy_recovery_executes_every_recovery_check(tmp_path: Path) -> None:
    """Healthy recovery still runs the whole chain: reuse evidence from the
    logs, the recovery upload, and the 12-hour refresh-loop probe."""
    docker = make_docker()
    sent: list[str] = []
    code = run_main(docker, tmp_path, acceptance_runner=_recording_runner(sent))
    assert code == 0
    assert sent == ["engine-up", "engine-outage", "engine-up"]
    assert any(argv[:2] == ["docker", "exec"] for argv in docker.invocations)
    report = (tmp_path / "acc" / "report.md").read_text(encoding="utf-8")
    assert "[x] health returned after restart" in report
    assert "[x] signature volume REUSED (no fresh bootstrap)" in report
    assert "[x] clean upload accepted again after recovery" in report
    assert "[x] freshclam refresh loop (sleep 43200) running" in report


def test_cleanup_failure_is_recorded_and_never_deletes_volumes(tmp_path: Path) -> None:
    """A failed `down` is recorded as a failure and never turns destructive."""
    docker = make_docker(fail_substrings=("down --remove-orphans",))
    code = run_main(docker, tmp_path)
    assert code == 1
    report = (tmp_path / "acc" / "report.md").read_text(encoding="utf-8")
    assert "isolated stack brought down" in report
    assert "self-audit" in report  # the full check log was still recorded
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)
    assert_never_targets_production(docker, make_plan())


def test_keyboard_interrupt_still_cleans_up_and_preserves_evidence(tmp_path: Path) -> None:
    """Ctrl-C mid-run: isolated cleanup and the report still happen."""
    docker = make_docker()

    def interrupting_runner(python: str, api: str, origin: str, mode: str) -> PBA.CommandResult:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_main(docker, tmp_path, acceptance_runner=interrupting_runner)
    assert any(argv[:2] == ["docker", "compose"] and "down" in argv for argv in docker.invocations)
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)
    assert (tmp_path / "acc" / "report.md").exists()
    assert_never_targets_production(docker, make_plan())


def test_report_write_failure_records_failure_without_crashing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unwritable report path must fail the run, not crash with a traceback
    that masks the check results — and cleanup must still have happened."""
    docker = make_docker()
    report_path = tmp_path / "acc" / "report.md"
    report_path.mkdir(parents=True)  # a directory cannot be written as a file
    code = run_main(docker, tmp_path)
    assert code == 1  # every check passed, but the evidence file failed
    assert "could not be written" in capsys.readouterr().out
    assert any(argv[:2] == ["docker", "compose"] and "down" in argv for argv in docker.invocations)
    assert not any(argv[:3] == ["docker", "volume", "rm"] for argv in docker.invocations)


# -------------------------------------------------- report redaction (Finding 4)
def test_redaction_protects_generated_tokens_but_not_env_values() -> None:
    """Regression: a short env value (POSTGRES_USER=mafhum) must not corrupt
    the generated project name, while the same value standalone IS redacted."""
    env = {"POSTGRES_USER": "mafhum", "SESSION_SECRET": "zz-secret-marker"}
    text = "project mafhum-clamav-acc-1 owned by mafhum leaked zz-secret-marker"
    out = PBA.redact_env_values(text, env, protect=("mafhum-clamav-acc-1",))
    assert "mafhum-clamav-acc-1" in out  # the generated token survives verbatim
    assert " owned by mafhum " not in out  # the standalone value is redacted
    assert "zz-secret-marker" not in out
    assert out.count("***REDACTED***") == 2


def test_redaction_covers_short_values_of_sensitive_keys() -> None:
    """A pathologically short secret (e.g. a 3-char password) is still
    redacted, while ordinary short values are not mangled."""
    env = {"POSTGRES_PASSWORD": "abc", "SESSION_SECRET": "xyz", "POSTGRES_USER": "db"}
    out = PBA.redact_env_values("password=abc secret=xyz user=db", env)
    assert "abc" not in out and "xyz" not in out
    assert "user=db" in out  # an ordinary 2-char username is not a secret
    assert out.count("***REDACTED***") == 2


def test_report_redacts_env_values_on_failure_paths(tmp_path: Path) -> None:
    """Env-file values echoed by failing docker commands never reach reports."""
    secret = "zzq-secret-marker-7f3a9b"
    env_content = ENV_CONTENT.replace(
        "SESSION_SECRET=not-a-real-secret-either", f"SESSION_SECRET={secret}"
    )
    assert secret in env_content

    # Failure path 1: config validation echoes the secret in its stderr.
    docker = make_docker(
        fail_substrings=("config -q",), fail_stderr=f"interpolation of {secret} failed"
    )
    assert run_main(docker, tmp_path, env_content=env_content) == 1
    report = (tmp_path / "acc" / "report.md").read_text(encoding="utf-8")
    assert secret not in report
    assert "***REDACTED***" in report

    # Failure path 2: the stack up echoes it as well.
    docker2 = make_docker(fail_substrings=("up -d db api",), fail_stderr=f"invalid value {secret}")
    assert run_main(docker2, tmp_path, env_content=env_content) == 1
    report2 = (tmp_path / "acc" / "report.md").read_text(encoding="utf-8")
    assert secret not in report2
    assert "***REDACTED***" in report2


# ------------------------------------------------------------------- self-audit
def test_self_audit_detects_a_production_targeting_command() -> None:
    plan = make_plan()
    problems = PBA.audit_invocations(
        [["docker", "compose", "--project-name", "mafhum-prod", "stop", "clamd"]], plan
    )
    assert any("production" in problem for problem in problems)
    problems = PBA.audit_invocations([["docker", "volume", "rm", "mafhum-prod_clamd_db"]], plan)
    assert problems
    problems = PBA.audit_invocations(
        [["docker", "compose", "stop", "clamd"]],
        plan,  # no --project-name at all
    )
    assert problems
    assert PBA.audit_invocations([], plan) == []


def test_source_contains_no_hardcoded_production_resources() -> None:
    """AST-level proof: no executable string may name a production resource.

    The reserved-name guard itself must use the literal ``mafhum-prod``
    (that is its purpose); docstrings and comments may explain it. Any other
    executable constant naming the production project, its containers or its
    volumes fails this test.
    """
    source = TOOL.read_text(encoding="utf-8")
    tree = ast.parse(source)

    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    offenders: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "mafhum-prod" in node.value.lower()
        ):
            if id(node) in docstrings:
                continue  # documentation, not an executable identifier
            if node.value.strip() == "mafhum-prod":
                continue  # the guard constants and their tests
            offenders.append(node.value[:100])
    assert offenders == [], f"executable production identifiers found: {offenders}"

    # The guard must actually exist and be consulted.
    assert frozenset({"mafhum-prod", "mafhum"}) == PBA.RESERVED_PROJECT_NAMES
    assert PBA.RESERVED_PROJECT_PREFIX == "mafhum-prod"
