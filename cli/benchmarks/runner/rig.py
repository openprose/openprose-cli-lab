"""Policy-frozen benchmark scheduling, collection, and deterministic analysis."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import random
import re
import signal
import statistics
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Protocol, Sequence


RAW_SCHEMA = "openprose.benchmark-raw/1"
SUMMARY_SCHEMA = "openprose.benchmark-summary/1"
CALCULATION_VERSION = "openprose-benchmark-calculation/2"
_SECRET_KEY = re.compile(
    r"(?:token|secret|password|api_?key|authorization|cookie|credential)", re.I
)
_ASSIGNMENT_SECRET = re.compile(
    r"\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|ACCESS_KEY)[A-Z0-9_]*)=([^\s]+)",
    re.I,
)
_BEARER_SECRET = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+|bearer\s+)[A-Za-z0-9._~+/=-]+"
)
_OPENAI_STYLE_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b")


@dataclass(frozen=True)
class TrialPlan:
    ordinal: int
    pair_id: str
    pair_index: int
    position: int
    phase: str
    action_id: str
    scorecard: str
    target_id: str
    surface: str
    comparison_group: str


@dataclass(frozen=True)
class Invocation:
    plan: TrialPlan
    target: Mapping[str, Any]
    action: Mapping[str, Any]
    timeout_ms: int
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    status: str
    wall_ms: float
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False
    cost_status: str = "unavailable"
    cost_usd: float | None = None
    observed_attempts: int | None = None
    declared_retries: int | None = None
    spans: Mapping[str, Any] | None = None
    diagnostic: Mapping[str, Any] | None = None
    process_evidence: Mapping[str, Any] | None = None

    @classmethod
    def success(
        cls,
        wall_ms: float,
        *,
        stdout: str = "",
        stderr: str = "",
        cost_status: str = "unavailable",
        cost_usd: float | None = None,
        observed_attempts: int | None = None,
        declared_retries: int | None = None,
        spans: Mapping[str, Any] | None = None,
    ) -> "Observation":
        return cls(
            "success",
            wall_ms,
            0,
            stdout,
            stderr,
            False,
            cost_status,
            cost_usd,
            observed_attempts,
            declared_retries,
            spans,
        )

    @classmethod
    def failure(
        cls,
        wall_ms: float,
        *,
        exit_code: int | None,
        stdout: str = "",
        stderr: str = "",
    ) -> "Observation":
        return cls("failure", wall_ms, exit_code, stdout, stderr)

    @classmethod
    def timeout(
        cls, wall_ms: float, *, stdout: str = "", stderr: str = ""
    ) -> "Observation":
        return cls("timeout", wall_ms, None, stdout, stderr)


class Driver(Protocol):
    """One invocation boundary; implementations must perform no implicit retry."""

    execution_boundary: str

    def invoke(self, invocation: Invocation) -> Observation:
        ...


class FakeDriver:
    """Deterministic provider-free driver for policy and analysis tests."""

    execution_boundary = "synthetic-in-process"

    def __init__(self, outcomes: Sequence[Observation] = ()) -> None:
        self._outcomes = list(outcomes)
        self.invocations: list[Invocation] = []

    def invoke(self, invocation: Invocation) -> Observation:
        self.invocations.append(invocation)
        if self._outcomes:
            return self._outcomes.pop(0)
        return Observation.success(float(len(self.invocations)))


class CommandDriver:
    """Direct argv driver with explicit platform containment and settlement evidence."""

    execution_boundary = "subprocess"

    def __init__(self, output_limit_bytes: int = 1_048_576) -> None:
        if output_limit_bytes < 1:
            raise ValueError("output_limit_bytes must be positive")
        self.output_limit_bytes = output_limit_bytes

    def invoke(self, invocation: Invocation) -> Observation:
        executable_value = invocation.target.get("_executionArtifact")
        if not isinstance(executable_value, str) or not executable_value:
            raise ValueError(
                "command benchmark target requires an admitted execution snapshot"
            )
        executable = executable_value
        argv = [
            executable,
            *[str(value) for value in invocation.action.get("argv", [])],
        ]
        environment = {
            str(name): str(value)
            for name, value in invocation.context.get("environment", {}).items()
        }
        environment.update(
            {
                str(name): str(value)
                for name, value in invocation.target.get("environment", {}).items()
            }
        )
        environment.update(
            {
                str(name): str(value)
                for name, value in invocation.action.get("environment", {}).items()
            }
        )
        request = invocation.action.get("stdinJson")
        return self.invoke_argv(
            argv,
            request,
            timeout_ms=invocation.timeout_ms,
            cwd=str(
                invocation.context.get("cwd", invocation.target.get("cwd", os.getcwd()))
            ),
            environment=environment,
        )

    def invoke_argv(
        self,
        argv: Sequence[str],
        stdin_json: Mapping[str, Any] | None,
        *,
        timeout_ms: int,
        cwd: str,
        environment: Mapping[str, str],
    ) -> Observation:
        if not argv or not all(isinstance(value, str) and value for value in argv):
            raise ValueError("command argv must contain nonempty strings")
        if timeout_ms < 1:
            raise ValueError("timeout_ms must be positive")
        containment = _process_containment_capability(os.name)
        creation_flags = 0
        start_new_session = os.name != "nt"
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        started = time.perf_counter_ns()
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=dict(environment),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=start_new_session,
                creationflags=creation_flags,
            )
        except OSError as error:
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            return Observation(
                status="failure",
                wall_ms=elapsed,
                exit_code=None,
                diagnostic={"kind": "spawn-failed", "error": str(error)},
                process_evidence={
                    "containment": containment,
                    "settlement": {
                        "status": "not-started",
                        "allowsSuccessfulTrial": False,
                        "directProcessExited": None,
                        "stdoutReaderSettled": None,
                        "stderrReaderSettled": None,
                        "readerErrors": [],
                        "cleanup": {"attempted": False, "status": "not-required"},
                        "authority": containment["authority"],
                    },
                },
            )

        stdout_state = _BoundedCapture(self.output_limit_bytes)
        stderr_state = _BoundedCapture(self.output_limit_bytes)
        stdout_reader = threading.Thread(
            target=_drain_pipe,
            args=(process.stdout, stdout_state),
            daemon=True,
        )
        stderr_reader = threading.Thread(
            target=_drain_pipe,
            args=(process.stderr, stderr_state),
            daemon=True,
        )
        stdout_reader.start()
        stderr_reader.start()
        if process.stdin is not None:
            try:
                if stdin_json is not None:
                    process.stdin.write(canonical_json_bytes(stdin_json))
                    process.stdin.write(b"\n")
                    process.stdin.flush()
            except BrokenPipeError:
                pass
            finally:
                process.stdin.close()

        timed_out = False
        cleanup: Mapping[str, Any] = {"attempted": False, "status": "not-required"}
        try:
            process.wait(timeout=timeout_ms / 1000)
        except subprocess.TimeoutExpired:
            timed_out = True
            cleanup = _terminate_process_group(process, containment)
        stdout_reader.join(timeout=1)
        stderr_reader.join(timeout=1)
        if (stdout_reader.is_alive() or stderr_reader.is_alive()) and not timed_out:
            cleanup = _terminate_process_group(process, containment)
            stdout_reader.join(timeout=1)
            stderr_reader.join(timeout=1)
        settlement = _settlement_evidence(
            containment,
            direct_process_exited=process.poll() is not None,
            stdout_reader_settled=not stdout_reader.is_alive(),
            stderr_reader_settled=not stderr_reader.is_alive(),
            reader_errors=[
                error
                for error in (stdout_state.reader_error, stderr_state.reader_error)
                if error is not None
            ],
            cleanup=cleanup,
        )
        process_evidence = {"containment": containment, "settlement": settlement}
        if stdout_reader.is_alive() and process.stdout is not None:
            process.stdout.close()
        if stderr_reader.is_alive() and process.stderr is not None:
            process.stderr.close()
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        stdout = stdout_state.text()
        stderr = stderr_state.text()
        if timed_out:
            return Observation(
                "timeout",
                elapsed,
                None,
                stdout,
                stderr,
                stdout_state.truncated or stderr_state.truncated,
                process_evidence=process_evidence,
            )

        status = (
            "success"
            if process.returncode == 0 and settlement["allowsSuccessfulTrial"]
            else "failure"
        )
        diagnostic = None
        if process.returncode == 0 and not settlement["allowsSuccessfulTrial"]:
            diagnostic = {"kind": "process-settlement-failed"}
        return Observation(
            status=status,
            wall_ms=elapsed,
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            output_truncated=stdout_state.truncated or stderr_state.truncated,
            diagnostic=diagnostic,
            process_evidence=process_evidence,
        )


class _BoundedCapture:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.parts: list[bytes] = []
        self.stored = 0
        self.total = 0
        self.reader_error: str | None = None

    def add(self, chunk: bytes) -> None:
        self.total += len(chunk)
        remaining = max(0, self.maximum - self.stored)
        if remaining:
            kept = chunk[:remaining]
            self.parts.append(kept)
            self.stored += len(kept)

    @property
    def truncated(self) -> bool:
        return self.total > self.stored

    def text(self) -> str:
        return b"".join(self.parts).decode("utf-8", errors="replace")


def _drain_pipe(pipe: Any, capture: _BoundedCapture) -> None:
    if pipe is None:
        return
    try:
        while True:
            chunk = pipe.read(8192)
            if not chunk:
                return
            capture.add(chunk)
    except OSError as error:
        capture.reader_error = type(error).__name__
    finally:
        pipe.close()


def _process_containment_capability(platform_name: str) -> dict[str, Any]:
    if platform_name == "nt":
        return {
            "platformFamily": "windows",
            "authority": "direct-child-only",
            "mechanism": "CREATE_NEW_PROCESS_GROUP-plus-direct-child-terminate",
            "releaseContainmentSupported": False,
            "nativeHelperRequired": True,
            "blocker": "native-job-object-process-host-not-integrated",
        }
    return {
        "platformFamily": "posix",
        "authority": "owned-process-group",
        "mechanism": "start-new-session-plus-killpg",
        "releaseContainmentSupported": False,
        "nativeHelperRequired": False,
        "blocker": "detached-descendant-containment-not-enforced",
    }


def _settlement_evidence(
    containment: Mapping[str, Any],
    *,
    direct_process_exited: bool,
    stdout_reader_settled: bool,
    stderr_reader_settled: bool,
    reader_errors: Sequence[str],
    cleanup: Mapping[str, Any],
) -> dict[str, Any]:
    cleanup_status = cleanup.get("status")
    settled = (
        direct_process_exited
        and stdout_reader_settled
        and stderr_reader_settled
        and not reader_errors
        and cleanup_status in {"not-required", "settled"}
    )
    return {
        "status": "settled" if settled else "not-settled",
        "allowsSuccessfulTrial": settled,
        "directProcessExited": direct_process_exited,
        "stdoutReaderSettled": stdout_reader_settled,
        "stderrReaderSettled": stderr_reader_settled,
        "readerErrors": list(reader_errors),
        "cleanup": dict(cleanup),
        "authority": containment["authority"],
    }


def _process_evidence_validation(
    execution_boundary: str,
    observation_status: str,
    process_evidence: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Validate closed subprocess evidence without trusting success flags alone."""

    def failed(reason: str) -> dict[str, str]:
        return {"status": "failed", "reason": reason}

    if execution_boundary == "synthetic-in-process":
        if process_evidence is not None:
            return failed("synthetic-process-evidence-forbidden")
        return {
            "status": "not-applicable",
            "reason": "synthetic-in-process-no-subprocess",
        }
    if process_evidence is None or not isinstance(process_evidence, Mapping):
        return failed("process-evidence-missing")
    if set(process_evidence) - {"containment", "settlement"}:
        return failed("process-evidence-unknown-field")
    if "containment" not in process_evidence:
        return failed("containment-missing")
    if "settlement" not in process_evidence:
        return failed("settlement-missing")

    containment = process_evidence["containment"]
    if not isinstance(containment, Mapping):
        return failed("containment-invalid-value")
    containment_fields = {
        "platformFamily",
        "authority",
        "mechanism",
        "releaseContainmentSupported",
        "nativeHelperRequired",
        "blocker",
    }
    if containment_fields - set(containment):
        return failed("containment-missing-field")
    if set(containment) - containment_fields:
        return failed("containment-unknown-field")
    platform_family = containment.get("platformFamily")
    if platform_family not in {"posix", "windows"}:
        return failed("containment-invalid-value")
    if (
        not isinstance(containment.get("authority"), str)
        or not containment.get("authority")
        or not isinstance(containment.get("mechanism"), str)
        or not containment.get("mechanism")
        or type(containment.get("releaseContainmentSupported")) is not bool
        or type(containment.get("nativeHelperRequired")) is not bool
        or (
            containment.get("blocker") is not None
            and (
                not isinstance(containment.get("blocker"), str)
                or not containment.get("blocker")
            )
        )
    ):
        return failed("containment-invalid-value")
    expected_containment = _process_containment_capability(
        "nt" if platform_family == "windows" else "posix"
    )
    if dict(containment) != expected_containment:
        return failed("containment-inconsistent")

    settlement = process_evidence["settlement"]
    if not isinstance(settlement, Mapping):
        return failed("settlement-invalid-value")
    settlement_fields = {
        "status",
        "allowsSuccessfulTrial",
        "directProcessExited",
        "stdoutReaderSettled",
        "stderrReaderSettled",
        "readerErrors",
        "cleanup",
        "authority",
    }
    if settlement_fields - set(settlement):
        return failed("settlement-missing-field")
    if set(settlement) - settlement_fields:
        return failed("settlement-unknown-field")
    status = settlement.get("status")
    if status not in {"settled", "not-settled", "not-started"}:
        return failed("settlement-invalid-value")
    allows_success = settlement.get("allowsSuccessfulTrial")
    if not isinstance(allows_success, bool):
        return failed("settlement-invalid-value")
    if settlement.get("authority") != containment.get("authority"):
        return failed("settlement-inconsistent")
    reader_errors = settlement.get("readerErrors")
    if not isinstance(reader_errors, list) or not all(
        isinstance(error, str) and error for error in reader_errors
    ):
        return failed("settlement-invalid-value")

    cleanup = settlement.get("cleanup")
    if not isinstance(cleanup, Mapping):
        return failed("cleanup-invalid-value")
    cleanup_required = {"attempted", "status"}
    cleanup_allowed = cleanup_required | {"authority", "signals", "actions"}
    if cleanup_required - set(cleanup):
        return failed("cleanup-missing-field")
    if set(cleanup) - cleanup_allowed:
        return failed("cleanup-unknown-field")
    cleanup_attempted = cleanup.get("attempted")
    cleanup_status = cleanup.get("status")
    if not isinstance(cleanup_attempted, bool) or cleanup_status not in {
        "not-required",
        "settled",
        "not-settled",
    }:
        return failed("cleanup-invalid-value")
    if cleanup_status == "not-required" and cleanup_attempted:
        return failed("cleanup-inconsistent")
    if cleanup_status in {"settled", "not-settled"} and not cleanup_attempted:
        return failed("cleanup-inconsistent")
    if cleanup_attempted and cleanup.get("authority") != containment.get("authority"):
        return failed("cleanup-inconsistent")
    for action_key in ("signals", "actions"):
        if action_key in cleanup and (
            not isinstance(cleanup[action_key], list)
            or not all(isinstance(value, str) for value in cleanup[action_key])
        ):
            return failed("cleanup-invalid-value")

    direct_exited = settlement.get("directProcessExited")
    stdout_settled = settlement.get("stdoutReaderSettled")
    stderr_settled = settlement.get("stderrReaderSettled")
    if status == "not-started":
        if any(
            value is not None
            for value in (direct_exited, stdout_settled, stderr_settled)
        ):
            return failed("settlement-inconsistent")
        if allows_success or reader_errors or cleanup_status != "not-required":
            return failed("settlement-inconsistent")
    else:
        if not all(
            isinstance(value, bool)
            for value in (direct_exited, stdout_settled, stderr_settled)
        ):
            return failed("settlement-invalid-value")
        computed_success = (
            direct_exited
            and stdout_settled
            and stderr_settled
            and not reader_errors
            and cleanup_status in {"not-required", "settled"}
        )
        expected_status = "settled" if computed_success else "not-settled"
        if status != expected_status or allows_success is not computed_success:
            return failed("settlement-inconsistent")
    if observation_status == "success" and allows_success is not True:
        return failed("successful-process-not-settled")
    return {"status": "passed", "reason": "complete-consistent-process-evidence"}


def _terminate_process_group(
    process: subprocess.Popen[bytes], containment: Mapping[str, Any]
) -> dict[str, Any]:
    if os.name == "nt":
        actions: list[str] = []
        if process.poll() is None:
            try:
                process.terminate()
                actions.append("terminate-direct-child")
                process.wait(timeout=0.2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    actions.append("kill-direct-child")
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        settled = process.poll() is not None
        return {
            "attempted": True,
            "status": "settled" if settled else "not-settled",
            "authority": containment["authority"],
            "actions": actions,
        }

    signals_sent: list[str] = []
    try:
        os.killpg(process.pid, signal.SIGTERM)
        signals_sent.append("SIGTERM")
    except ProcessLookupError:
        pass
    except OSError:
        signals_sent.append("SIGTERM-error")
    if process.poll() is None:
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass
    if _posix_group_exists(process.pid):
        try:
            os.killpg(process.pid, signal.SIGKILL)
            signals_sent.append("SIGKILL")
        except ProcessLookupError:
            pass
        except OSError:
            signals_sent.append("SIGKILL-error")
    if process.poll() is None:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
    settled = process.poll() is not None and not _posix_group_exists(process.pid)
    return {
        "attempted": True,
        "status": "settled" if settled else "not-settled",
        "authority": containment["authority"],
        "signals": signals_sent,
    }


def _posix_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _mechanical_validation(
    observation: Observation,
    action: Mapping[str, Any],
    target: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    expectation = action.get("expectation")
    if observation.status != "success":
        return {
            "status": "not-run",
            "kind": expectation,
            "reason": "process-not-successful",
        }
    if expectation is None:
        return {"status": "not-applicable", "kind": None, "observed": {}}
    if expectation == "version":
        first_line = (
            observation.stdout.strip().splitlines()[0]
            if observation.stdout.strip()
            else ""
        )
        passed = first_line.startswith("prose ")
        return {
            "status": "passed" if passed else "failed",
            "kind": "version",
            "reason": None if passed else "version-output-shape",
            "observed": {"versionLine": redact_text(first_line)},
        }
    try:
        value = json.loads(observation.stdout)
    except (json.JSONDecodeError, TypeError):
        return {
            "status": "failed",
            "kind": expectation,
            "reason": "stdout-is-not-one-json-value",
            "observed": {},
        }
    if not isinstance(value, dict):
        return {
            "status": "failed",
            "kind": expectation,
            "reason": "stdout-json-is-not-object",
            "observed": {},
        }
    observed: dict[str, Any] = {"schema": value.get("schema")}
    passed = False
    if expectation == "doctor":
        observed.update(_doctor_identity(value))
        passed = (
            value.get("schema") == "openprose.doctor-report/1"
            and value.get("ready") is True
            and observed.get("harness") == target.get("harness")
            and observed.get("transport") == target.get("transport")
        )
    elif expectation == "dry-run":
        selection = value.get("selection", {})
        image = value.get("languageImage", {})
        observed.update(
            {
                "harness": selection.get("harness"),
                "transport": selection.get("transport"),
                "model": selection.get("model"),
                "imageSha256": image.get("sha256"),
                "imageReleaseEligible": image.get("releaseEligible"),
            }
        )
        passed = (
            value.get("schema") == "openprose.runner-dry-run-report/1"
            and value.get("wouldStartModel") is False
            and selection.get("harness") == target.get("harness")
            and selection.get("transport") == target.get("transport")
            and image.get("sha256") == profile["image"]["sha256"]
            and image.get("releaseEligible") is False
        )
    elif expectation == "runner-result":
        adapter = value.get("adapter", {})
        image = value.get("languageImage", {})
        terminal = value.get("terminal", {})
        observed.update(
            {
                "harness": target.get("harness"),
                "transport": value.get("transport"),
                "model": target.get("model", {}).get("id"),
                "adapterId": adapter.get("id"),
                "harnessVersion": adapter.get("harnessVersion"),
                "harnessDescriptorSha256": adapter.get("descriptorDigestSha256"),
                "imageSha256": image.get("sha256"),
                "taskSha256": value.get("digests", {}).get("taskSha256"),
            }
        )
        passed = (
            value.get("schema") == "openprose.runner-result/1"
            and value.get("transport") == target.get("transport")
            and adapter.get("id") == "mock/fake-process"
            and adapter.get("descriptorDigestSha256")
            == target.get("harnessDescriptorSha256")
            and image.get("sha256") == profile["image"]["sha256"]
            and terminal.get("transportCompleted") is True
            and terminal.get("terminalEventObserved") is True
        )
    return {
        "status": "passed" if passed else "failed",
        "kind": expectation,
        "reason": None if passed else "mechanical-expectation-not-met",
        "observed": redact(observed),
    }


def _doctor_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value.get("harness"), Mapping):
        harness = value["harness"].get("id")
        transport = value.get("transport")
    else:
        harness = value.get("selectedHarness")
        transport = value.get("selectedTransport")
    image = value.get("image", {})
    return {
        "harness": harness,
        "transport": transport,
        "imageSha256": image.get("sha256") if isinstance(image, Mapping) else None,
        "imageReleaseEligible": image.get("releaseEligible")
        if isinstance(image, Mapping)
        else None,
    }


def build_schedule(
    profile: Mapping[str, Any], policy: Mapping[str, Any]
) -> list[TrialPlan]:
    """Builds deterministic, paired blocks without crossing surface boundaries."""
    seed = int(profile["seed"])
    randomizer = random.Random(seed)
    targets = sorted(profile["targets"], key=lambda target: target["id"])
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for target in targets:
        key = (str(target["surface"]), str(target["comparisonGroup"]))
        grouped.setdefault(key, []).append(target)
    schedule: list[TrialPlan] = []
    ordinal = 0
    phases = (
        ("warmup", int(policy["warmups"])),
        ("measurement", int(policy["repetitions"])),
    )
    for action in profile["actions"]:
        for phase, count in phases:
            for pair_index in range(count):
                for (surface, comparison_group), members in sorted(grouped.items()):
                    ordered_targets = list(members)
                    randomizer.shuffle(ordered_targets)
                    pair_id = f"{surface}/{comparison_group}/{action['id']}/{phase}/{pair_index}"  # noqa: E501
                    for position, target in enumerate(ordered_targets):
                        schedule.append(
                            TrialPlan(
                                ordinal=ordinal,
                                pair_id=pair_id,
                                pair_index=pair_index,
                                position=position,
                                phase=phase,
                                action_id=str(action["id"]),
                                scorecard=str(action["scorecard"]),
                                target_id=str(target["id"]),
                                surface=surface,
                                comparison_group=comparison_group,
                            )
                        )
                        ordinal += 1
    return schedule


def validate_profile_claims(profile: Mapping[str, Any]) -> None:
    image = profile.get("image", {})
    corpus = profile.get("corpus", {})
    if (
        image.get("releaseEligible") is not True
        and profile.get("releaseEligible") is True
    ):
        raise ValueError("release claim is forbidden for a non-release image")
    if corpus.get("status") != "available" and profile.get("semanticStatus") not in {
        "unknown",
        "not-applicable",
    }:
        raise ValueError(
            "semantic status cannot be claimed without the canonical corpus"
        )
    if profile.get("proseCompleteClaim") is not False:
        raise ValueError("Prose Complete claim is forbidden without canonical evidence")
    surfaces = {target.get("surface") for target in profile.get("targets", [])}
    if None in surfaces:
        raise ValueError("every target requires an explicit surface")


def run_benchmark(
    profile: Mapping[str, Any],
    policy: Mapping[str, Any],
    driver: Driver,
    *,
    machine: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validate_profile_claims(profile)
    schedule = build_schedule(profile, policy)
    targets = {target["id"]: target for target in profile["targets"]}
    actions = {action["id"]: action for action in profile["actions"]}
    stop_conditions = profile["stopConditions"]
    maximum_cost = float(stop_conditions["maximumAuthoritativeCostUsd"])
    maximum_timeouts = int(stop_conditions["maximumTimeouts"])
    authoritative_cost = 0.0
    unavailable_cost_trials = 0
    timeouts = 0
    stop_reason: str | None = None
    trials: list[dict[str, Any]] = []
    retry_trusted = True
    retry_visibility_complete = True
    invocation_context = dict(context or {})
    execution_boundary_kind = getattr(driver, "execution_boundary", "subprocess")
    if execution_boundary_kind not in {"subprocess", "synthetic-in-process"}:
        raise ValueError(
            f"unsupported driver execution boundary: {execution_boundary_kind}"
        )
    execution_boundary = {
        "kind": execution_boundary_kind,
        "processEvidenceRequired": execution_boundary_kind == "subprocess",
    }

    for plan in schedule:
        base = asdict(plan)
        if stop_reason is not None:
            trials.append(
                {
                    **base,
                    "status": "not-run",
                    "processStatus": "not-run",
                    "failureKind": "policy-stop",
                    "stopReason": stop_reason,
                    "executionBoundary": execution_boundary,
                    "wallMs": None,
                    "exitCode": None,
                    "stdout": "",
                    "stderr": "",
                    "outputTruncated": False,
                    "processEvidence": {
                        "containment": {"status": "not-run"},
                        "settlement": {
                            "status": "not-run",
                            "allowsSuccessfulTrial": False,
                        },
                    },
                    "processEvidenceValidation": {
                        "status": "not-run",
                        "reason": "policy-stop",
                    },
                    "validation": {
                        "status": "not-run",
                        "kind": actions[plan.action_id].get("expectation"),
                        "reason": "policy-stop",
                    },
                    "cost": {"status": "unavailable", "usd": None},
                    "retry": {
                        "visibility": "not-run",
                        "observedAttempts": None,
                        "declaredRetries": None,
                        "hiddenRetrySuspected": False,
                    },
                    "timingComponents": derive_residual(None, None),
                }
            )
            continue
        invocation = Invocation(
            plan=plan,
            target=targets[plan.target_id],
            action=actions[plan.action_id],
            timeout_ms=int(policy["timeoutAccounting"]["perRunMs"]),
            context=invocation_context,
        )
        observed = driver.invoke(invocation)
        _validate_observation_metadata(observed)
        if observed.status not in {"success", "failure", "timeout"}:
            raise ValueError(f"driver returned invalid status: {observed.status}")
        if observed.cost_status not in {"authoritative", "unavailable"}:
            raise ValueError("cost status must be authoritative or unavailable")
        validation = _mechanical_validation(
            observed,
            actions[plan.action_id],
            targets[plan.target_id],
            profile,
        )
        process_evidence_validation = _process_evidence_validation(
            execution_boundary_kind,
            observed.status,
            observed.process_evidence,
        )
        effective_status = observed.status
        process_evidence_failed = process_evidence_validation["status"] == "failed"
        if observed.status == "success" and validation["status"] == "failed":
            effective_status = "failure"
        if observed.status == "success" and process_evidence_failed:
            effective_status = "failure"
        if observed.cost_status == "authoritative":
            if observed.cost_usd is None or observed.cost_usd < 0:
                raise ValueError("authoritative cost requires a nonnegative USD value")
            authoritative_cost += observed.cost_usd
        else:
            unavailable_cost_trials += 1
        if effective_status == "timeout":
            timeouts += 1
        retry_visibility = (
            "unavailable" if observed.observed_attempts is None else "observed"
        )
        if observed.observed_attempts is None:
            retry_visibility_complete = False
        hidden_retry = (
            observed.observed_attempts is not None
            and observed.observed_attempts > 1 + (observed.declared_retries or 0)
        )
        if hidden_retry:
            retry_trusted = False
        failure_kind = None
        if effective_status == "failure":
            if (
                process_evidence_validation["reason"]
                == "successful-process-not-settled"
            ):
                failure_kind = "process-settlement-failure"
            elif process_evidence_failed:
                failure_kind = "process-evidence-invalid"
            elif observed.status == "success":
                failure_kind = "validator-failure"
            elif (observed.diagnostic or {}).get("kind") == "process-settlement-failed":
                failure_kind = "process-settlement-failure"
            else:
                failure_kind = "nonzero-or-spawn-failure"
        elif effective_status == "timeout":
            failure_kind = "timeout"
        trials.append(
            {
                **base,
                "status": effective_status,
                "processStatus": observed.status,
                "failureKind": failure_kind,
                "executionBoundary": execution_boundary,
                "wallMs": round(float(observed.wall_ms), 6),
                "exitCode": observed.exit_code,
                "stdout": redact_text(
                    observed.stdout, invocation_context.get("redactionLiterals", [])
                ),
                "stderr": redact_text(
                    observed.stderr, invocation_context.get("redactionLiterals", [])
                ),
                "outputTruncated": observed.output_truncated,
                "diagnostic": redact(observed.diagnostic),
                "processEvidence": redact(
                    observed.process_evidence
                    if observed.process_evidence is not None
                    else {
                        "containment": {"status": "unavailable-from-driver"},
                        "settlement": {
                            "status": "unavailable-from-driver",
                            "allowsSuccessfulTrial": None,
                        },
                    }
                ),
                "processEvidenceValidation": process_evidence_validation,
                "validation": validation,
                "cost": {"status": observed.cost_status, "usd": observed.cost_usd},
                "retry": {
                    "visibility": retry_visibility,
                    "observedAttempts": observed.observed_attempts,
                    "declaredRetries": observed.declared_retries,
                    "hiddenRetrySuspected": hidden_retry,
                },
                "timingComponents": derive_residual(observed.wall_ms, observed.spans),
            }
        )
        if authoritative_cost >= maximum_cost:
            stop_reason = "authoritative-cost-limit"
        elif timeouts >= maximum_timeouts:
            stop_reason = "timeout-limit"

    return {
        "schema": RAW_SCHEMA,
        "calculationVersion": CALCULATION_VERSION,
        "policy": {
            "id": policy["policyId"],
            "version": policy["policyVersion"],
            "frozenAt": policy["frozenAt"],
            "sha256": profile.get("policyArtifactSha256") or sha256_json(policy),
            "canonicalJsonSha256": sha256_json(policy),
            "seed": int(profile["seed"]),
        },
        "profile": {
            "id": profile["profileId"],
            "version": profile["profileVersion"],
            "sha256": profile.get("_profileArtifactSha256")
            or sha256_json(_without_private(profile)),
            "resolvedCanonicalJsonSha256": sha256_json(_without_private(profile)),
        },
        "machine": redact(dict(machine)),
        "qualification": {
            "machineQualified": bool(machine.get("qualified", False)),
            "retryAccountingTrusted": retry_trusted and retry_visibility_complete,
            "retryVisibilityComplete": retry_visibility_complete,
            "releaseEligible": False,
        },
        "claims": {
            "releaseEligible": False,
            "semanticStatus": profile["semanticStatus"],
            "proseComplete": False,
            "surfaceComparison": "within-surface-only",
            "sentinelOnly": image_is_sentinel(profile),
        },
        "identities": redact(
            {
                "image": profile["image"],
                "corpus": profile["corpus"],
                "program": profile["program"],
                "validator": profile["validator"],
                "targets": [_public_target(target) for target in profile["targets"]],
            }
        ),
        "plannedTrials": len(schedule),
        "trials": trials,
        "accounting": {
            "authoritativeCostUsd": round(authoritative_cost, 12),
            "unavailableCostTrials": unavailable_cost_trials,
            "timeouts": timeouts,
            "declaredRetries": sum(
                int(trial["retry"]["declaredRetries"] or 0) for trial in trials
            ),
            "hiddenRetrySuspicions": sum(
                1 for trial in trials if trial["retry"]["hiddenRetrySuspected"]
            ),
            "unavailableRetryTrials": sum(
                1 for trial in trials if trial["retry"]["visibility"] == "unavailable"
            ),
        },
        "stop": {"triggered": stop_reason is not None, "reason": stop_reason},
    }


def _validate_observation_metadata(observation: Observation) -> None:
    """Validate facts supplied by the runner-owned driver boundary.

    Command subjects never populate this contract from stdout. Custom trusted
    drivers must still provide closed, finite values so malformed observations
    fail deterministically before arithmetic or report generation.
    """

    def invalid(reason: str) -> None:
        raise ValueError(f"observation metadata is invalid: {reason}")

    if observation.cost_status not in {"authoritative", "unavailable"}:
        invalid("cost status")
    if observation.cost_status == "unavailable":
        if observation.cost_usd is not None:
            invalid("unavailable cost must not include USD")
    elif (
        not isinstance(observation.cost_usd, (int, float))
        or isinstance(observation.cost_usd, bool)
        or not math.isfinite(float(observation.cost_usd))
        or float(observation.cost_usd) < 0
    ):
        invalid("authoritative cost must be a finite nonnegative number")

    if observation.observed_attempts is not None and (
        not isinstance(observation.observed_attempts, int)
        or isinstance(observation.observed_attempts, bool)
        or observation.observed_attempts < 1
    ):
        invalid("observed attempts must be a positive integer")
    if observation.declared_retries is not None and (
        not isinstance(observation.declared_retries, int)
        or isinstance(observation.declared_retries, bool)
        or observation.declared_retries < 0
    ):
        invalid("declared retries must be a nonnegative integer")

    spans = observation.spans
    if spans is None:
        return
    required = {"complete", "clock", "providerMs", "toolMs"}
    if not isinstance(spans, Mapping) or set(spans) != required:
        invalid("spans must use the closed timing contract")
    if type(spans["complete"]) is not bool:
        invalid("span completeness must be boolean")
    if not isinstance(spans["clock"], str) or not spans["clock"]:
        invalid("span clock must be a nonempty string")
    for name in ("providerMs", "toolMs"):
        value = spans[name]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) < 0
        ):
            invalid(f"{name} must be a finite nonnegative number")


def summarize(raw: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    measurements = [trial for trial in raw["trials"] if trial["phase"] == "measurement"]
    seed = int(raw["policy"]["seed"])
    confidence = policy["confidence"]
    scorecards: dict[str, Any] = {
        "transport": _timing_scorecard(measurements, "transport", confidence, seed),
        "developerExperience": _timing_scorecard(measurements, "dx", confidence, seed),
        "agentEfficiency": _agent_scorecard(measurements),
        "cost": _cost_scorecard(measurements),
        "semanticQuality": _semantic_scorecard(raw),
    }
    scorecards["transport"]["coverage"] = {
        "externalWallClock": "measured",
        "processSpawnAndProtocol": "included-not-subtracted",
        "firstNormalizedEvent": "unavailable-in-final-json-smoke",
        "peakRss": "unavailable",
        "cpu": "unavailable",
        "cancellationCleanup": "recorded-per-trial",
    }
    scorecards["transport"]["processExecution"] = _process_evidence_summary(
        measurements
    )
    scorecards["developerExperience"]["artifactAndInstallSurface"] = {
        target["id"]: {
            "artifactBytes": target.get("artifactBytes"),
            "artifactSha256": target.get("artifactSha256"),
            "artifactKind": target.get("artifactKind"),
            "installSurface": target.get("installSurface"),
        }
        for target in raw["identities"]["targets"]
    }
    scorecards["developerExperience"]["dimensions"] = {
        "coldStartup": "cold-startup-version",
        "warmStartup": "warm-startup-version",
        "doctor": "doctor",
        "dryRun": "dry-run",
        "packageInstall": "not-measured-development-artifacts-only",
        "compositeWinner": None,
    }
    return {
        "schema": SUMMARY_SCHEMA,
        "calculationVersion": CALCULATION_VERSION,
        "source": {
            "rawSchema": raw["schema"],
            "rawArtifactSha256": raw.get("_rawArtifactSha256"),
            "policySha256": raw["policy"]["sha256"],
            "profileSha256": raw["profile"]["sha256"],
        },
        "qualification": raw["qualification"],
        "claims": raw["claims"],
        "rules": {
            "paired": True,
            "seed": seed,
            "warmupsExcludedFromEstimatesButRetainedRaw": True,
            "failuresRetained": True,
            "timeoutsRetained": True,
            "outliersDropped": 0,
            "confidence": confidence,
            "residualFallback": policy["residualTiming"]["fallback"],
            "winnerCollapsed": False,
            "successLatencyEligibility": "status-success-with-wall-time",
            "pairedLatencyEligibility": "both-members-success-with-wall-time",
            "allAttemptDurationReportedSeparately": True,
        },
        "regressionBudgets": policy["regressionBudgets"],
        "scorecards": scorecards,
    }


def _trial_success_eligible(trial: Mapping[str, Any]) -> bool:
    if trial.get("status") != "success" or trial.get("wallMs") is None:
        return False
    boundary = trial.get("executionBoundary")
    if isinstance(boundary, Mapping):
        boundary_kind = boundary.get("kind")
    else:
        # Historical raw evidence predates the explicit marker and was command-driven.
        boundary_kind = "subprocess"
    if boundary_kind == "synthetic-in-process":
        validation = trial.get("processEvidenceValidation")
        return (
            isinstance(validation, Mapping)
            and validation.get("status") == "not-applicable"
            and validation.get("reason") == "synthetic-in-process-no-subprocess"
        )
    if boundary_kind != "subprocess":
        return False
    validation = _process_evidence_validation(
        "subprocess",
        str(trial.get("processStatus", trial.get("status"))),
        trial.get("processEvidence")
        if isinstance(trial.get("processEvidence"), Mapping)
        else None,
    )
    return validation["status"] == "passed"


def _timing_scorecard(
    trials: Sequence[Mapping[str, Any]],
    scorecard: str,
    confidence: Mapping[str, Any],
    seed: int,
) -> dict[str, Any]:
    selected = [trial for trial in trials if trial["scorecard"] == scorecard]
    if not selected:
        return {"status": "not-applicable", "surfaces": {}}
    surfaces: dict[str, Any] = {}
    grouped = itertools.groupby(
        sorted(selected, key=lambda trial: (trial["surface"], trial["action_id"])),
        key=lambda trial: (trial["surface"], trial["action_id"]),
    )
    for (surface, action_id), action_iter in grouped:
        action_trials = list(action_iter)
        target_results: dict[str, Any] = {}
        for target_id, target_iter in itertools.groupby(
            sorted(action_trials, key=lambda trial: trial["target_id"]),
            key=lambda trial: trial["target_id"],
        ):
            target_trials = list(target_iter)
            stable_seed = _derived_seed(seed, surface, action_id, target_id)
            target_results[target_id] = _trial_distribution(
                target_trials,
                int(confidence["resamples"]),
                float(confidence["level"]),
                stable_seed,
            )
        comparisons = _paired_comparisons(
            action_trials,
            int(confidence["resamples"]),
            float(confidence["level"]),
            seed,
            surface,
            action_id,
        )
        surface_entry = surfaces.setdefault(surface, {"actions": {}})
        surface_entry["actions"][action_id] = {
            "targets": target_results,
            "pairedDifferencesMs": comparisons,
            "winner": None,
        }
    return {"status": "measured", "surfaces": surfaces}


def _trial_distribution(
    trials: Sequence[Mapping[str, Any]],
    resamples: int,
    level: float,
    seed: int,
) -> dict[str, Any]:
    executed = [trial for trial in trials if trial["status"] != "not-run"]
    success_values = [
        float(trial["wallMs"]) for trial in trials if _trial_success_eligible(trial)
    ]
    attempt_values = [
        float(trial["wallMs"]) for trial in executed if trial["wallMs"] is not None
    ]
    exclusions = {
        "failure": sum(trial["status"] == "failure" for trial in trials),
        "timeout": sum(trial["status"] == "timeout" for trial in trials),
        "not-run": sum(trial["status"] == "not-run" for trial in trials),
        "success-missing-wall-time": sum(
            trial["status"] == "success" and trial["wallMs"] is None for trial in trials
        ),
    }
    return {
        "planned": len(trials),
        "executed": len(executed),
        "successes": sum(trial["status"] == "success" for trial in trials),
        "failures": sum(trial["status"] == "failure" for trial in trials),
        "timeouts": sum(trial["status"] == "timeout" for trial in trials),
        "notRun": sum(trial["status"] == "not-run" for trial in trials),
        "successLatencyEligibility": {
            "eligible": len(success_values),
            "excluded": len(trials) - len(success_values),
            "exclusionReasons": exclusions,
        },
        "successLatencyMs": _duration_statistics(
            success_values, resamples, level, seed
        ),
        "allAttemptDurationMs": _duration_statistics(
            attempt_values,
            resamples,
            level,
            _derived_seed(seed, "all-attempt-duration"),
        ),
    }


def _paired_comparisons(
    trials: Sequence[Mapping[str, Any]],
    resamples: int,
    level: float,
    seed: int,
    surface: str,
    action_id: str,
) -> list[dict[str, Any]]:
    targets = sorted({str(trial["target_id"]) for trial in trials})
    by_pair: dict[str, dict[str, Mapping[str, Any]]] = {}
    for trial in trials:
        by_pair.setdefault(str(trial["pair_id"]), {})[str(trial["target_id"])] = trial
    comparisons: list[dict[str, Any]] = []
    for left, right in itertools.combinations(targets, 2):
        differences: list[float] = []
        retained_pairs = 0
        exclusion_reasons: dict[str, int] = {}
        for members in by_pair.values():
            if left not in members or right not in members:
                continue
            retained_pairs += 1
            left_wall = members[left]["wallMs"]
            right_wall = members[right]["wallMs"]
            reason = None
            left_eligible = _trial_success_eligible(members[left])
            right_eligible = _trial_success_eligible(members[right])
            if not left_eligible and not right_eligible:
                reason = "both-not-success"
            elif not left_eligible:
                reason = "left-not-success"
            elif not right_eligible:
                reason = "right-not-success"
            elif left_wall is None or right_wall is None:
                reason = "missing-wall-time"
            if reason is not None:
                exclusion_reasons[reason] = exclusion_reasons.get(reason, 0) + 1
                continue
            differences.append(float(left_wall) - float(right_wall))
        comparisons.append(
            {
                "left": left,
                "right": right,
                "definition": "left-success-wall-ms-minus-right-success-wall-ms",
                "plannedPairs": retained_pairs,
                "eligibleSuccessPairs": len(differences),
                "excludedPairs": retained_pairs - len(differences),
                "exclusionReasons": exclusion_reasons,
                "samples": len(differences),
                "p50": _round_or_none(
                    statistics.median(differences) if differences else None
                ),
                "pairedBootstrapMedianCi": bootstrap_interval(
                    differences,
                    resamples,
                    level,
                    _derived_seed(seed, surface, action_id, left, right),
                ),
            }
        )
    return comparisons


def _duration_statistics(
    values: Sequence[float],
    resamples: int,
    level: float,
    seed: int,
) -> dict[str, Any]:
    return {
        "samples": len(values),
        "mean": _round_or_none(statistics.fmean(values) if values else None),
        "p50": _round_or_none(statistics.median(values) if values else None),
        "bootstrapMedianCi": bootstrap_interval(values, resamples, level, seed),
    }


def _process_evidence_summary(
    trials: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    targets: dict[str, Any] = {}
    for target_id in sorted({str(trial["target_id"]) for trial in trials}):
        selected = [trial for trial in trials if trial["target_id"] == target_id]
        capabilities = {
            canonical_json_bytes(trial["processEvidence"]["containment"]).decode(
                "utf-8"
            )
            for trial in selected
        }
        containment = (
            json.loads(next(iter(capabilities)))
            if len(capabilities) == 1
            else {"status": "inconsistent-across-trials"}
        )
        settlement_counts: dict[str, int] = {}
        for trial in selected:
            status = str(trial["processEvidence"]["settlement"]["status"])
            settlement_counts[status] = settlement_counts.get(status, 0) + 1
        targets[target_id] = {
            "containment": containment,
            "settlementCounts": settlement_counts,
            "allExecutedTrialsSettled": all(
                trial["status"] == "not-run"
                or trial["processEvidence"]["settlement"]["status"] == "settled"
                for trial in selected
            ),
            "releaseContainmentSupported": containment.get(
                "releaseContainmentSupported", False
            ),
        }
    return {"status": "recorded", "targets": targets}


def _agent_scorecard(trials: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    targets: dict[str, Any] = {}
    for target_id in sorted({str(trial["target_id"]) for trial in trials}):
        selected = [trial for trial in trials if trial["target_id"] == target_id]
        residuals = [
            trial["timingComponents"]["residualWrapperMs"]
            for trial in selected
            if _trial_success_eligible(trial)
            and trial["timingComponents"]["residualWrapperMs"] is not None
        ]
        targets[target_id] = {
            "modelCalls": {"status": "not-applicable", "count": None},
            "tokens": {"status": "unavailable", "count": None},
            "retries": {
                "declared": sum(
                    int(trial["retry"]["declaredRetries"] or 0) for trial in selected
                ),
                "hiddenSuspicions": sum(
                    trial["retry"]["hiddenRetrySuspected"] for trial in selected
                ),
            },
            "residualWrapperMs": {
                "status": "measured" if residuals else "unavailable",
                "samples": len(residuals),
                "p50": _round_or_none(
                    statistics.median(residuals) if residuals else None
                ),
            },
        }
    return {
        "status": "not-applicable-for-provider-free-sentinel"
        if not trials
        else "limited",
        "targets": targets,
        "winner": None,
    }


def _cost_scorecard(trials: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    targets: dict[str, Any] = {}
    for target_id in sorted({str(trial["target_id"]) for trial in trials}):
        selected = [trial for trial in trials if trial["target_id"] == target_id]
        authoritative = [
            float(trial["cost"]["usd"])
            for trial in selected
            if _trial_success_eligible(trial)
            and trial["cost"]["status"] == "authoritative"
        ]
        targets[target_id] = {
            "authoritative": {
                "samples": len(authoritative),
                "totalUsd": round(sum(authoritative), 12) if authoritative else None,
            },
            "unavailableSamples": sum(
                _trial_success_eligible(trial)
                and trial["cost"]["status"] == "unavailable"
                for trial in selected
            ),
            "estimatedOrImputedUsd": None,
        }
    return {"status": "reported-without-imputation", "targets": targets, "winner": None}


def _semantic_scorecard(raw: Mapping[str, Any]) -> dict[str, Any]:
    surfaces = sorted({trial["surface"] for trial in raw["trials"]})
    return {
        "status": raw["claims"]["semanticStatus"],
        "surfaces": {
            surface: {
                "evaluated": False,
                "semanticStatus": raw["claims"]["semanticStatus"],
                "proseComplete": False,
                "reason": "canonical-language-image-terminal-corpus-unavailable",
            }
            for surface in surfaces
        },
        "winner": None,
    }


def derive_residual(
    wall_ms: float | None, spans: Mapping[str, Any] | None
) -> dict[str, Any]:
    if not spans:
        return {
            "providerMs": None,
            "toolMs": None,
            "residualWrapperMs": None,
            "reason": "spans-unavailable",
        }
    provider = _optional_float(spans.get("providerMs"))
    tool = _optional_float(spans.get("toolMs"))
    result = {
        "providerMs": provider,
        "toolMs": tool,
        "residualWrapperMs": None,
        "reason": None,
    }
    if (
        spans.get("complete") is not True
        or provider is None
        or tool is None
        or wall_ms is None
    ):
        result["reason"] = "incomplete-spans"
        return result
    if spans.get("clock") not in {"common-monotonic", "correlated-validated"}:
        result["reason"] = "uncorrelated-clocks"
        return result
    residual = float(wall_ms) - provider - tool
    if residual < 0:
        result["reason"] = "inconsistent-spans"
        return result
    result["residualWrapperMs"] = round(residual, 6)
    result["reason"] = "complete-correlated-spans"
    return result


def bootstrap_interval(
    values: Sequence[float],
    resamples: int,
    level: float,
    seed: int,
) -> dict[str, float] | None:
    if not values:
        return None
    if resamples < 100 or not 0.5 < level < 1:
        raise ValueError("invalid bootstrap policy")
    samples = [float(value) for value in values]
    randomizer = random.Random(seed)
    estimates = []
    for _ in range(resamples):
        draw = [samples[randomizer.randrange(len(samples))] for _ in samples]
        estimates.append(statistics.median(draw))
    estimates.sort()
    alpha = (1 - level) / 2
    return {
        "level": level,
        "lower": round(_percentile(estimates, alpha), 6),
        "upper": round(_percentile(estimates, 1 - alpha), 6),
        "resamples": resamples,
    }


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    index = probability * (len(sorted_values) - 1)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(sorted_values[lower])
    weight = index - lower
    return float(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight)


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "<redacted>" if _SECRET_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(value: str, literals: Sequence[str] = ()) -> str:
    cleaned = _ASSIGNMENT_SECRET.sub(
        lambda match: f"{match.group(1)}=<redacted>", value
    )
    cleaned = _BEARER_SECRET.sub(lambda match: f"{match.group(1)}<redacted>", cleaned)
    cleaned = _OPENAI_STYLE_SECRET.sub("<redacted>", cleaned)
    for literal in sorted((item for item in literals if item), key=len, reverse=True):
        cleaned = cleaned.replace(literal, "<BENCHMARK_TEMP>")
    return cleaned


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def image_is_sentinel(profile: Mapping[str, Any]) -> bool:
    image = profile.get("image", {})
    return image.get("releaseEligible") is False or str(
        image.get("version", "")
    ).startswith("sentinel")


def _derived_seed(seed: int, *labels: str) -> int:
    digest = hashlib.sha256(
        canonical_json_bytes({"seed": seed, "labels": list(labels)})
    ).digest()
    return int.from_bytes(digest[:8], "big")


def _optional_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def _public_target(target: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): redact(value)
        for key, value in target.items()
        if not str(key).startswith("_")
    }


def _without_private(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_private(item)
            for key, item in value.items()
            if not str(key).startswith("_")
        }
    if isinstance(value, (list, tuple)):
        return [_without_private(item) for item in value]
    return value
