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
    ) -> None:
        self.calls: list[list[str]] = []
        self.projects = list(projects or [])
        self.preexisting_label_volumes = preexisting_label_volumes or {}
        self.created_label_volumes = created_label_volumes or {}
        self.volume_info = volume_info or {}
        self.existing_container_names = existing_container_names or set()
        self.fail_substrings = fail_substrings
        self.ups = 0  # compose `up` commands executed so far

    def __call__(self, args: list[str], timeout: float = 1800) -> PBA.CommandResult:
        self.calls.append(list(args))
        joined = " ".join(args)
        for fragment in self.fail_substrings:
            if fragment in joined:
                return PBA.CommandResult(1, "", f"simulated failure: {fragment}")

        if args[:2] == ["docker", "--version"]:
            return PBA.CommandResult(0, "Docker version 27.0.0 (fake)", "")
        if args[:3] == ["docker", "compose", "version"]:
            return PBA.CommandResult(0, "Docker Compose version v2.29.0 (fake)", "")
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
            return PBA.CommandResult(0, "healthy", "")
        if args[:2] == ["docker", "logs"]:
            return PBA.CommandResult(
                0,
                "[clamd] no signature database on the volume; running freshclam\n"
                "[clamd] daily.cvd updated (signature evidence)\n"
                "[clamd] reusing the signature database on the volume\n"
                "[clamd] starting daemon on TCP 3310\n",
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
) -> int:
    env_file = tmp_path / "env"
    env_file.write_text(ENV_CONTENT, encoding="utf-8")
    return PBA.main(
        ["--env-file", str(env_file), *(argv or [])],
        docker=docker,
        workdir=tmp_path / "acc",
        python="python",
        acceptance_runner=lambda python, api, origin, mode: PBA.CommandResult(0, "", ""),
        http_probe=lambda url: (200, READY_BODY),
        sleep=lambda seconds: None,
        project_name=project,
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
    problems = PBA.preflight(docker, make_plan())
    assert any("ambiguity" in problem for problem in problems)


def test_container_name_collision_aborts() -> None:
    docker = make_docker(existing_container_names={f"{SAFE_PROJECT}-clamd-1"})
    problems = PBA.preflight(docker, make_plan())
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

    # The full run wires the same loopback bind into the generated override.
    docker = make_docker()
    run_main(docker, tmp_path, ["--api-port", "8123"], project=SAFE_PROJECT)
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
    code = run_main(docker, tmp_path)
    assert code == 1  # the failure was recorded
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
