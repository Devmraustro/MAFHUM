"""Offline validation of the MAFHUM monitoring configuration.

This is the repository-side half of the monitoring contract promised in
docs/MONITORING.md. It answers, without needing a Prometheus binary:

1. Do ``prometheus.yml``, ``alert.rules.yml`` and ``alertmanager.yml`` parse
   as YAML, and do they reference files and receivers that actually exist?
2. Is every alert rule's ``expr`` syntactically valid PromQL? (checked with
   the ``promql-parser`` crate binding — the same grammar family promtool
   uses; promtool itself remains the authoritative check on the monitoring
   host, and this tool explicitly does not claim to replace it.)
3. Does every metric referenced by a rule exist — either emitted by the
   application's exporter (``apps/api/src/mafhum_api/api/v1/admin.py``) or a
   Prometheus built-in (``up``)?
4. Do the alert rules actually mean what docs/MONITORING.md says they mean?
   Each rule is evaluated over synthetic series by a small AST-driven
   evaluator (below), asserting both the firing and the non-firing cases,
   including the ``for:`` persistence window.
5. Is the Alertmanager routing tree internally consistent (every receiver
   referenced by a route exists, every matcher parses, timings are ordered)?

With ``--live URL --token TOKEN`` the tool additionally scrapes a running
exporter and verifies that every application metric referenced by a rule is
present in the live exposition — closing the loop between config and reality.

Usage:
    python tools/monitoring/validate_monitoring.py
    python tools/monitoring/validate_monitoring.py \
        --live https://api.example.com --token "$METRICS_SCRAPE_TOKEN"

Exit code 0 means every check passed. Any FAIL line is a defect.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

try:
    from promql_parser import parse as parse_expr
except ImportError as exc:  # pragma: no cover - dependency guard
    raise SystemExit(
        "promql-parser is required: pip install -e 'apps/api[dev]' (or pip install promql-parser)"
    ) from exc

REPO_ROOT = Path(__file__).resolve().parents[2]
PROM_DIR = REPO_ROOT / "deploy" / "prometheus"
EXPORTER_SOURCE = REPO_ROOT / "apps" / "api" / "src" / "mafhum_api" / "api" / "v1" / "admin.py"

# Prometheus series that exist without the application emitting them.
BUILTIN_METRICS = {"up"}

# PromQL functions / keywords / aggregations that are not metric names.
NON_METRIC_IDENTIFIERS = {
    "and",
    "or",
    "unless",
    "by",
    "without",
    "on",
    "ignoring",
    "group_left",
    "group_right",
    "offset",
    "bool",
    "avg",
    "count",
    "sum",
    "min",
    "max",
    "stddev",
    "stdvar",
    "topk",
    "bottomk",
    "count_values",
    "rate",
    "irate",
    "increase",
    "delta",
    "idelta",
    "deriv",
    "predict_linear",
    "absent",
    "absent_over_time",
    "histogram_quantile",
    "label_replace",
    "label_join",
    "vector",
    "scalar",
    "time",
    "timestamp",
    "clamp",
    "clamp_max",
    "clamp_min",
    "round",
    "abs",
    "ceil",
    "floor",
    "exp",
    "ln",
    "log2",
    "log10",
    "sqrt",
    "sgn",
    "sort",
    "sort_desc",
    "resets",
    "changes",
    "holt_winters",
    "atan",
    "atan2",
    "sin",
    "cos",
    "tan",
    "asin",
    "acos",
    "sinh",
    "cosh",
    "tanh",
    "asinh",
    "acosh",
    "atanh",
    "pi",
    "rad",
    "deg",
    "year",
    "month",
    "day_of_month",
    "day_of_week",
    "day_of_year",
    "days_in_month",
    "hour",
    "minute",
    "second",
    "startswith",
    "endswith",
}

failures: list[str] = []
passes = 0


def check(label: str, condition: bool, detail: str = "") -> bool:
    global passes
    mark = "PASS" if condition else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"[{mark}] {label}{suffix}", flush=True)
    if condition:
        passes += 1
    else:
        failures.append(f"{label}{suffix}")
    return condition


# --------------------------------------------------------------------- helpers
_DURATION_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>ms|s|m|h|d|w)$")
_DURATION_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: Any) -> float | None:
    """Parse a Prometheus/Alertmanager duration like ``2m`` into seconds."""
    if not isinstance(text, str):
        return None
    match = _DURATION_RE.match(text.strip())
    if not match:
        return None
    return float(match.group("value")) * _DURATION_UNITS[match.group("unit")]


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


# ------------------------------------------------------------- series modelling
@dataclass
class Series:
    """One synthetic time series for the scenario evaluator."""

    metric: str
    labels: dict[str, str] = field(default_factory=dict)
    points: list[tuple[float, float]] = field(default_factory=list)

    def at(self, ts: float, lookback: float = 300.0) -> float | None:
        """Instant value at ``ts`` (latest sample within the lookback)."""
        best: float | None = None
        for point_ts, value in self.points:
            if point_ts <= ts and point_ts > ts - lookback:
                best = value
        return best

    def value_in_window(self, ts: float, window: float) -> tuple[float, float] | None:
        """(first, last) sample value inside ``(ts - window, ts]``."""
        in_window = [v for t, v in self.points if ts - window < t <= ts]
        if not in_window:
            return None
        return in_window[0], in_window[-1]


@dataclass
class EvalResult:
    """Result of evaluating an expression: zero or more labelled values."""

    values: list[tuple[dict[str, str], float]] = field(default_factory=list)

    @property
    def present(self) -> bool:
        return bool(self.values)


def _matcher_text(selector: Any) -> str:
    return str(getattr(selector, "matchers", "") or "")


def _parse_matchers(text: str) -> list[tuple[str, str, str]]:
    """Parse ``{a="x", b=~"y"}`` into (label, op, value) triples."""
    triples: list[tuple[str, str, str]] = []
    for match in re.finditer(r'(\w+)\s*(=~|!~|=|!=)\s*"((?:[^"\\]|\\.)*)"', text):
        triples.append((match.group(1), match.group(2), match.group(3)))
    return triples


def _series_matches(series: Series, metric: str, matchers: list[tuple[str, str, str]]) -> bool:
    if series.metric != metric:
        return False
    for label, op, expected in matchers:
        actual = series.labels.get(label)
        if op == "=" and actual != expected:
            return False
        if op == "!=" and actual == expected:
            return False
        if op == "=~" and (actual is None or not re.search(expected, actual)):
            return False
        if op == "!~" and (actual is not None and re.search(expected, actual)):
            return False
    return True


class MiniEvaluator:
    """AST-driven evaluator for the expression shapes used by the alert rules.

    Supported node types (exactly what ``deploy/prometheus/alert.rules.yml``
    uses): instant vector selectors with label matchers, number literals,
    binary comparisons (``==``, ``>``, ``and``), ``increase(range-vector)``
    and ``absent(instant-vector)``.

    Simplifications, stated openly: ``increase`` is computed as
    ``last - first`` over the window without extrapolation, and the instant
    lookback is 5 minutes. Both match the intent of the rules; neither is a
    full reimplementation of PromQL. promtool on the monitoring host stays
    the authoritative evaluation.
    """

    def __init__(self, series: list[Series], at: float) -> None:
        self.series = series
        self.at = at

    def evaluate(self, node: Any) -> EvalResult:
        type_name = type(node).__name__
        if type_name == "VectorSelector":
            matchers = _parse_matchers(_matcher_text(node))
            name = getattr(node, "name", None)
            if name is None:  # bare selector like {job="mafhum"}
                matched = [s for s in self.series if _series_matches(s, s.metric, matchers)]
            else:
                matched = [s for s in self.series if _series_matches(s, name, matchers)]
            values: list[tuple[dict[str, str], float]] = []
            for series in matched:
                value = series.at(self.at)
                if value is not None:
                    values.append(({"__name__": series.metric, **series.labels}, value))
            return EvalResult(values)

        if type_name == "NumberLiteral":
            return EvalResult([({}, float(str(node)))])

        if type_name == "MatrixSelector":
            raise ValueError("range selector used outside a function")

        if type_name == "Call":
            function = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", str(node))
            fname = function.group(1) if function else ""
            argument = node.args[0]
            if fname == "increase":
                if type(argument).__name__ != "MatrixSelector":
                    raise ValueError("increase() needs a range vector")
                selector = argument.vector_selector
                window = self._range_seconds(argument)
                matchers = _parse_matchers(_matcher_text(selector))
                name = getattr(selector, "name", None)
                values = []
                for series in self.series:
                    if not _series_matches(series, name or series.metric, matchers):
                        continue
                    window_values = series.value_in_window(self.at, window)
                    if window_values is None:
                        continue
                    first, last = window_values
                    values.append(({"__name__": series.metric, **series.labels}, last - first))
                return EvalResult(values)
            if fname == "absent":
                inner = self.evaluate(argument)
                if inner.present:
                    return EvalResult([])
                return EvalResult([({}, 1.0)])
            raise ValueError(f"unsupported function in rule: {fname}")

        if type_name == "BinaryExpr":
            operator = str(node.op)
            lhs = self.evaluate(node.lhs)
            if operator == "and":
                rhs = self.evaluate(node.rhs)
                if not rhs.present:
                    return EvalResult([])
                return lhs
            rhs = self.evaluate(node.rhs)
            comparison = {
                "==": lambda a, b: a == b,
                "!=": lambda a, b: a != b,
                ">": lambda a, b: a > b,
                "<": lambda a, b: a < b,
                ">=": lambda a, b: a >= b,
                "<=": lambda a, b: a <= b,
            }.get(operator)
            if comparison is None:
                raise ValueError(f"unsupported binary operator: {operator}")
            values = []
            for labels, lvalue in lhs.values:
                if not rhs.values:
                    continue
                if comparison(lvalue, rhs.values[0][1]):
                    values.append((labels, lvalue))
            return EvalResult(values)

        raise ValueError(f"unsupported PromQL node: {type_name}")

    @staticmethod
    def _range_seconds(matrix_selector: Any) -> float:
        text = str(matrix_selector.range)  # e.g. "0:15:00"
        parts = [float(part) for part in text.split(":")]
        while len(parts) < 3:
            parts.append(0.0)
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds


# ------------------------------------------------------------------- the checks
def check_prometheus_config(config: dict[str, Any]) -> None:
    jobs = {job["job_name"]: job for job in config.get("scrape_configs", [])}
    check(
        "prometheus.yml: mafhum scrape job present",
        "mafhum" in jobs,
        detail=str(sorted(jobs)),
    )
    mafhum = jobs.get("mafhum", {})
    check(
        "prometheus.yml: metrics_path is the admin metrics endpoint",
        mafhum.get("metrics_path") == "/api/v1/admin/metrics",
        detail=str(mafhum.get("metrics_path")),
    )
    check(
        "prometheus.yml: bearer_token_file declared (no anonymous scrape)",
        bool(mafhum.get("bearer_token_file")),
        detail=str(mafhum.get("bearer_token_file")),
    )
    check(
        "prometheus.yml: internal-network target, not a public origin",
        mafhum.get("static_configs", [{}])[0].get("targets") == ["api:8000"],
        detail=str(mafhum.get("static_configs")),
    )
    check(
        "prometheus.yml: self-scrape job for monitor-death detection",
        "prometheus" in jobs,
    )
    rule_files = config.get("rule_files", [])
    for rule_file in rule_files:
        # Container paths like /etc/prometheus/alert.rules.yml map onto the
        # same file in deploy/prometheus/.
        local = PROM_DIR / Path(rule_file).name
        check(f"prometheus.yml: rule file exists ({rule_file})", local.is_file())

    alerting = config.get("alerting", {}).get("alertmanagers", [])
    check(
        "prometheus.yml: alertmanager target configured",
        bool(alerting) and bool(alerting[0].get("static_configs")),
    )


def _extract_referenced_metrics(expression: str) -> set[str]:
    # Label matchers ({job="mafhum"}, {status="failed"}) and range selectors
    # ([15m]) contribute identifiers that are not metric names — remove them
    # before scanning for identifiers.
    stripped = re.sub(r"\{[^}]*\}", " ", expression)
    stripped = re.sub(r"\[[^\]]*\]", " ", stripped)
    identifiers = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", stripped))
    return {ident for ident in identifiers if ident not in NON_METRIC_IDENTIFIERS}


def _emitted_metrics() -> set[str]:
    """Metric names the application exporter actually emits."""
    source = EXPORTER_SOURCE.read_text(encoding="utf-8")
    # The exposition is built from Python string literals such as
    # "# TYPE mafhum_up gauge", so anchor on the "# TYPE" marker itself.
    emitted = set(re.findall(r"#\s*TYPE\s+(\S+)\s+(?:gauge|counter)", source))
    return emitted


def check_rules(rules_doc: dict[str, Any], emitted: set[str]) -> dict[str, str]:
    """Validate structure + syntax + metric contract. Returns {alert: expr}."""
    expressions: dict[str, str] = {}
    groups = rules_doc.get("groups", [])
    total_rules = 0
    for group in groups:
        for rule in group.get("rules", []):
            total_rules += 1
            alert = rule.get("alert", "")
            expressions[alert] = rule.get("expr", "")
            check(
                f"rule {alert}: required fields present",
                all(
                    [
                        alert,
                        rule.get("expr"),
                        rule.get("for"),
                        rule.get("labels"),
                        rule.get("annotations"),
                    ]
                ),
            )
            severity = (rule.get("labels") or {}).get("severity")
            check(
                f"rule {alert}: severity is critical or warning",
                severity in {"critical", "warning"},
                detail=str(severity),
            )
            annotations = rule.get("annotations") or {}
            check(
                f"rule {alert}: summary and description annotated",
                bool(annotations.get("summary")) and bool(annotations.get("description")),
            )
            try:
                parse_expr(rule.get("expr", ""))
                check(f"rule {alert}: expr is valid PromQL", True)
            except Exception as exc:  # promql_parser raises on syntax errors
                check(f"rule {alert}: expr is valid PromQL", False, detail=str(exc))
            check(
                f"rule {alert}: 'for' is a valid duration",
                parse_duration(rule.get("for")) is not None,
                detail=str(rule.get("for")),
            )
            runbook = annotations.get("runbook")
            if runbook:
                # Runbook URLs point at blobs on main; verify the file exists
                # in this checkout so a doc rename cannot silently 404.
                match = re.search(r"/blob/main/(.+)$", runbook)
                exists = match and (REPO_ROOT / match.group(1)).is_file()
                check(f"rule {alert}: runbook target exists in repo", bool(exists), detail=runbook)
    check("rules: expected rule count (7)", total_rules == 7, detail=f"found {total_rules}")

    referenced: set[str] = set()
    for expression in expressions.values():
        referenced |= _extract_referenced_metrics(expression)
    unknown = referenced - emitted - BUILTIN_METRICS
    check(
        "contract: every rule metric is emitted by the exporter (or builtin)",
        not unknown,
        detail=f"unknown: {sorted(unknown)}" if unknown else f"{len(referenced)} metrics verified",
    )
    return expressions


def check_alertmanager(config: dict[str, Any]) -> None:
    receivers = {receiver["name"] for receiver in config.get("receivers", [])}
    route = config.get("route", {})
    check(
        "alertmanager.yml: default receiver exists",
        route.get("receiver") in receivers,
        detail=f"{route.get('receiver')} vs {sorted(receivers)}",
    )
    routes = route.get("routes", [])
    for index, sub_route in enumerate(routes):
        check(
            f"alertmanager.yml: route[{index}] receiver exists",
            sub_route.get("receiver") in receivers,
            detail=str(sub_route.get("receiver")),
        )
        for matcher in sub_route.get("matchers", []):
            check(
                f"alertmanager.yml: route[{index}] matcher parses",
                _matcher_parses(matcher),
                detail=matcher,
            )
    for key in ("group_wait", "group_interval", "repeat_interval"):
        check(
            f"alertmanager.yml: {key} is a valid duration",
            parse_duration(route.get(key)) is not None,
            detail=str(route.get(key)),
        )
    group_wait = parse_duration(route.get("group_wait"))
    repeat = parse_duration(route.get("repeat_interval"))
    check(
        "alertmanager.yml: repeat_interval is longer than group_wait",
        group_wait is not None and repeat is not None and repeat > group_wait,
    )
    for index, inhibit in enumerate(config.get("inhibit_rules", [])):
        check(
            f"alertmanager.yml: inhibit[{index}] has source and target matchers",
            bool(inhibit.get("source_matchers")) and bool(inhibit.get("target_matchers")),
        )
        for matcher in inhibit.get("source_matchers", []) + inhibit.get("target_matchers", []):
            check(
                f"alertmanager.yml: inhibit[{index}] matcher parses",
                _matcher_parses(matcher),
                detail=matcher,
            )


def _matcher_parses(matcher: str) -> bool:
    """Alertmanager matchers share PromQL label-matcher syntax."""
    try:
        parse_expr("placeholder_metric{" + matcher + "}")
        return True
    except Exception:
        return False


# -------------------------------------------------------------------- scenarios
def _run_scenario(
    label: str,
    expression: str,
    for_seconds: float,
    series: list[Series],
    evaluation_time: float,
    step: float,
    expect_firing: bool,
) -> None:
    """Evaluate ``expression`` over ``for_seconds`` up to ``evaluation_time``.

    A rule is FIRING only when the condition held at every step of the
    ``for:`` window ending at the evaluation time (approximating how
    Prometheus promotes pending -> firing).
    """
    try:
        ast = parse_expr(expression)
    except Exception as exc:
        check(f"scenario {label}: expr parses", False, detail=str(exc))
        return
    steps = max(1, round(for_seconds / step))
    held = True
    for back in range(steps):
        instant = evaluation_time - back * step
        result = MiniEvaluator(series, instant).evaluate(ast)
        if not result.present:
            held = False
            break
    fired = held
    check(
        f"scenario {label}: {'fires' if expect_firing else 'does NOT fire'} as documented",
        fired == expect_firing,
        detail=f"condition_held={held} at t={evaluation_time:.0f}s",
    )


def run_scenarios(expressions: dict[str, str], durations: dict[str, float]) -> None:
    """Synthetic-series behaviour tests for every rule, both polarities."""
    horizon = 10 * 3600.0  # generous synthetic timeline

    def constant(metric: str, value: float, labels: dict[str, str] | None = None) -> Series:
        # A point every 60 s so the 5-minute instant lookback always resolves.
        return Series(
            metric,
            labels or {},
            [(t, value) for t in range(0, int(horizon) + 1, 60)],
        )

    # --- MafhumApiDown: up{job="mafhum"} == 0, for 2m ----------------------
    expr = expressions["MafhumApiDown"]
    _run_scenario(
        "MafhumApiDown/down-for-3m",
        expr,
        durations["MafhumApiDown"],
        [constant("up", 0, {"job": "mafhum"})],
        3000,
        30,
        expect_firing=True,
    )
    _run_scenario(
        "MafhumApiDown/up",
        expr,
        durations["MafhumApiDown"],
        [constant("up", 1, {"job": "mafhum"})],
        3000,
        30,
        expect_firing=False,
    )
    _run_scenario(
        "MafhumApiDown/other-job-down-is-ignored",
        expr,
        durations["MafhumApiDown"],
        [constant("up", 0, {"job": "other"})],
        3000,
        30,
        expect_firing=False,
    )

    # --- MafhumScrapeTargetMissing: absent(up{job="mafhum"}), for 5m --------
    expr = expressions["MafhumScrapeTargetMissing"]
    _run_scenario(
        "MafhumScrapeTargetMissing/target-gone",
        expr,
        durations["MafhumScrapeTargetMissing"],
        [],
        3000,
        30,
        expect_firing=True,
    )
    _run_scenario(
        "MafhumScrapeTargetMissing/target-present",
        expr,
        durations["MafhumScrapeTargetMissing"],
        [constant("up", 1, {"job": "mafhum"})],
        3000,
        30,
        expect_firing=False,
    )

    # --- MafhumMalwareScannerUnavailable: required==1 and available==0 -----
    expr = expressions["MafhumMalwareScannerUnavailable"]
    _run_scenario(
        "MafhumMalwareScannerUnavailable/required-and-down",
        expr,
        durations["MafhumMalwareScannerUnavailable"],
        [
            constant("mafhum_malware_scan_required", 1),
            constant("mafhum_malware_scanner_available", 0),
        ],
        3000,
        30,
        expect_firing=True,
    )
    _run_scenario(
        "MafhumMalwareScannerUnavailable/dev-box-no-engine-required",
        expr,
        durations["MafhumMalwareScannerUnavailable"],
        [
            constant("mafhum_malware_scan_required", 0),
            constant("mafhum_malware_scanner_available", 0),
        ],
        3000,
        30,
        expect_firing=False,
    )
    _run_scenario(
        "MafhumMalwareScannerUnavailable/required-and-healthy",
        expr,
        durations["MafhumMalwareScannerUnavailable"],
        [
            constant("mafhum_malware_scan_required", 1),
            constant("mafhum_malware_scanner_available", 1),
        ],
        3000,
        30,
        expect_firing=False,
    )

    # --- MafhumDeniedEventsSpike: increase(...[15m]) > 20, for 5m ----------
    expr = expressions["MafhumDeniedEventsSpike"]

    # Counters sampled every 60 s. With ``for: 5m`` the condition must hold at
    # every evaluation instant of a 5-minute window, so the sustained-denied
    # scenario keeps a constant rate of ``total_rise`` per 15 minutes from
    # t=1800 onwards; every 15-minute window ending after that sees exactly
    # ``total_rise`` denied events.
    def counter_series(total_rise: float) -> Series:
        points = []
        for t in range(0, 3001, 60):
            elapsed_rise = max(0.0, t - 1800) * (total_rise / 900.0)
            points.append((float(t), 100 + elapsed_rise))
        return Series("mafhum_audit_denied_total", {}, points)

    _run_scenario(
        "MafhumDeniedEventsSpike/sustained-30-per-15m",
        expr,
        durations["MafhumDeniedEventsSpike"],
        [counter_series(30)],
        3000,
        30,
        expect_firing=True,
    )
    _run_scenario(
        "MafhumDeniedEventsSpike/sustained-4-per-15m",
        expr,
        durations["MafhumDeniedEventsSpike"],
        [counter_series(4)],
        3000,
        30,
        expect_firing=False,
    )

    # --- MafhumDocumentsStuckProcessing: pending > 0, for 30m --------------
    expr = expressions["MafhumDocumentsStuckProcessing"]
    _run_scenario(
        "MafhumDocumentsStuckProcessing/pending-90m",
        expr,
        durations["MafhumDocumentsStuckProcessing"],
        [constant("mafhum_processing_pending", 3)],
        5400,
        60,
        expect_firing=True,
    )
    _run_scenario(
        "MafhumDocumentsStuckProcessing/pending-10m-only",
        expr,
        durations["MafhumDocumentsStuckProcessing"],
        [
            Series(
                "mafhum_processing_pending",
                {},
                [(float(t), 3 if t <= 600 else 0) for t in range(0, 7201, 60)],
            )
        ],
        1800,
        60,
        expect_firing=False,
    )
    _run_scenario(
        "MafhumDocumentsStuckProcessing/idle",
        expr,
        durations["MafhumDocumentsStuckProcessing"],
        [constant("mafhum_processing_pending", 0)],
        5400,
        60,
        expect_firing=False,
    )

    # --- MafhumFailedDocuments: documents_total{status="failed"} > 10 ------
    expr = expressions["MafhumFailedDocuments"]
    _run_scenario(
        "MafhumFailedDocuments/11-failed",
        expr,
        durations["MafhumFailedDocuments"],
        [constant("mafhum_documents_total", 11, {"status": "failed"})],
        3000,
        30,
        expect_firing=True,
    )
    _run_scenario(
        "MafhumFailedDocuments/exactly-10-is-not-over",
        expr,
        durations["MafhumFailedDocuments"],
        [constant("mafhum_documents_total", 10, {"status": "failed"})],
        3000,
        30,
        expect_firing=False,
    )
    _run_scenario(
        "MafhumFailedDocuments/ready-status-is-ignored",
        expr,
        durations["MafhumFailedDocuments"],
        [constant("mafhum_documents_total", 50, {"status": "ready"})],
        3000,
        30,
        expect_firing=False,
    )

    # --- MafhumAuditTableGrowth: audit_events_total > 1000000, for 1h ------
    expr = expressions["MafhumAuditTableGrowth"]
    _run_scenario(
        "MafhumAuditTableGrowth/over-a-million",
        expr,
        durations["MafhumAuditTableGrowth"],
        [constant("mafhum_audit_events_total", 1_000_001)],
        5400,
        120,
        expect_firing=True,
    )
    _run_scenario(
        "MafhumAuditTableGrowth/exactly-a-million",
        expr,
        durations["MafhumAuditTableGrowth"],
        [constant("mafhum_audit_events_total", 1_000_000)],
        5400,
        120,
        expect_firing=False,
    )


# --------------------------------------------------------------- live scraping
def check_live(base_url: str, token: str, emitted: set[str]) -> None:
    try:
        response = httpx.get(
            base_url.rstrip("/") + "/api/v1/admin/metrics",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
            follow_redirects=False,
        )
        response.raise_for_status()
        body = response.text
    except Exception as exc:
        check("live: exporter reachable with bearer token", False, detail=str(exc))
        return
    check("live: exporter reachable with bearer token", True)
    live_metrics = set(re.findall(r"^#\s+TYPE\s+(\S+)\s+", body, re.MULTILINE))
    missing = emitted - live_metrics
    check(
        "live: every exporter metric present in the live exposition",
        not missing,
        detail=(
            f"missing: {sorted(missing)}" if missing else f"{len(live_metrics)} series families"
        ),
    )


# ------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", help="base URL of a running API to scrape")
    parser.add_argument("--token", help="METRICS_SCRAPE_TOKEN for the live scrape")
    args = parser.parse_args(argv)

    print(f"== MAFHUM monitoring validation ({PROM_DIR}) ==")

    prometheus_path = PROM_DIR / "prometheus.yml"
    rules_path = PROM_DIR / "alert.rules.yml"
    alertmanager_path = PROM_DIR / "alertmanager.yml"
    for path in (prometheus_path, rules_path, alertmanager_path):
        check(f"YAML parse: {path.name}", path.is_file() and load_yaml(path) is not None)
    config = load_yaml(prometheus_path)
    rules_doc = load_yaml(rules_path)
    alertmanager = load_yaml(alertmanager_path)

    check_prometheus_config(config)
    emitted = _emitted_metrics()
    check(
        "contract: exporter source parsed",
        len(emitted) >= 10,
        detail=f"{len(emitted)} metrics found in {EXPORTER_SOURCE.name}",
    )
    expressions = check_rules(rules_doc, emitted)
    durations = {
        rule.get("alert", ""): parse_duration(rule.get("for")) or 0
        for group in rules_doc.get("groups", [])
        for rule in group.get("rules", [])
    }
    check_alertmanager(alertmanager)

    print("== rule behaviour scenarios ==")
    run_scenarios(expressions, durations)

    if args.live:
        if not args.token:
            print("[FAIL] --live requires --token")
            failures.append("--live requires --token")
        else:
            print("== live scrape ==")
            check_live(args.live, args.token, emitted)

    print()
    if failures:
        print(f"RESULT: MONITORING VALIDATION FAILED ({len(failures)} failure(s))")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"RESULT: ALL MONITORING CHECKS PASSED ({passes} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
