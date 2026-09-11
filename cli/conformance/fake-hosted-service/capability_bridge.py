"""Least-privilege local capability fixture for the hosted-agent placement probe."""

from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
import stat
import subprocess
import threading
from typing import Any, Callable, Iterable, Mapping, Sequence


WIRE_SCHEMA = "openprose.hosted-wire/1"
MAX_IO_BYTES = 1024 * 1024
SAFE_ENVIRONMENT = frozenset({"LANG", "LC_ALL", "TERM"})
SHELL_NAMES = frozenset({
    "ash", "bash", "cmd", "cmd.exe", "csh", "dash", "fish", "ksh",
    "powershell", "powershell.exe", "pwsh", "sh", "tcsh", "zsh",
})

Executor = Callable[
    [list[str], Path, dict[str, str], int, int],
    tuple[int, bytes, bytes],
]


class CapabilityRefusal(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LocalCapabilityBridge:
    """A test-only bridge with no ambient discovery, shell, or network surface."""

    def __init__(
        self,
        workspace: Path,
        *,
        read_allowlist: Iterable[str] = (),
        write_allowlist: Iterable[str] = (),
        executable_allowlist: Mapping[str, str | Sequence[str]] | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be a directory")
        self.executable_allowlist = {
            name: ([target] if isinstance(target, str) else list(target))
            for name, target in (executable_allowlist or {}).items()
        }
        self.read_allowlist = frozenset(read_allowlist)
        self.write_allowlist = frozenset(write_allowlist)
        self.executor = executor or self._execute
        self._uses_default_executor = executor is None
        for name, command in self.executable_allowlist.items():
            if (
                not name
                or Path(name).name != name
                or name.lower() in SHELL_NAMES
                or not command
                or any(not isinstance(value, str) or "\0" in value for value in command)
            ):
                raise ValueError("executable allowlist entry is invalid")
            if self._uses_default_executor and (
                not Path(command[0]).is_absolute()
                or Path(command[0]).name.lower() in SHELL_NAMES
            ):
                raise ValueError("default executor requires an absolute non-shell executable")
        self.requested = 0
        self.completed = 0
        self.rejected = 0
        self.rejection_codes: dict[str, int] = {}
        self._direct_child_reaped: bool | None = None
        self._max_retained_output_bytes_observed = 0

    def handle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self.requested += 1
        try:
            self._validate_envelope(request)
            operation = request["operation"]
            arguments = request["arguments"]
            if operation == "workspace.read":
                result = self._read(arguments)
            elif operation == "workspace.write":
                result = self._write(arguments)
            elif operation == "process.exec":
                result = self._exec(arguments)
            else:
                raise CapabilityRefusal(
                    "CAPABILITY_OPERATION_REJECTED",
                    "The requested local capability is not allowlisted.",
                )
        except CapabilityRefusal as refusal:
            self.rejected += 1
            self.rejection_codes[refusal.code] = self.rejection_codes.get(refusal.code, 0) + 1
            return self._response(
                request,
                "capability.error",
                error={"code": refusal.code, "message": refusal.message},
            )
        self.completed += 1
        return self._response(request, "capability.result", result=result)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema": "openprose.local-capability-evidence/1",
            "requested": self.requested,
            "completed": self.completed,
            "rejected": self.rejected,
            "rejectionCodes": dict(sorted(self.rejection_codes.items())),
            "filesystemPathsConfined": True,
            "processWorkingDirectoryConfined": True,
            "processFilesystemSandbox": "unsupported",
            "defaultDeny": True,
            "shellEnabled": False,
            "networkEnabled": False,
            "outputCapture": (
                "bounded-default-executor"
                if self._uses_default_executor
                else "external-executor-unverified"
            ),
            "maxRetainedOutputBytesObserved": self._max_retained_output_bytes_observed,
            "directChildReaped": self._direct_child_reaped,
            "processContainment": (
                "direct-child-only"
                if self._uses_default_executor
                else "external-executor"
            ),
            "descendantContainment": "unsupported",
        }

    def _validate_envelope(self, request: Mapping[str, Any]) -> None:
        required = {
            "schema", "type", "requestId", "runId", "sequence",
            "correlationId", "operation", "arguments",
        }
        if set(request) != required:
            raise CapabilityRefusal(
                "CAPABILITY_OPERATION_REJECTED",
                "The capability envelope has unknown or missing fields.",
            )
        if request.get("schema") != WIRE_SCHEMA or request.get("type") != "capability.request":
            raise CapabilityRefusal(
                "CAPABILITY_OPERATION_REJECTED",
                "The capability envelope version or type is unsupported.",
            )
        for name in ("requestId", "runId", "correlationId"):
            if not isinstance(request.get(name), str) or not request[name]:
                raise CapabilityRefusal(
                    "CAPABILITY_OPERATION_REJECTED",
                    "The capability envelope correlation is invalid.",
                )
        if not isinstance(request.get("arguments"), Mapping):
            raise CapabilityRefusal(
                "CAPABILITY_OPERATION_REJECTED",
                "The capability arguments must be an object.",
            )

    def _read(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self._closed_arguments(arguments, {"path", "maxBytes"})
        if arguments["path"] not in self.read_allowlist:
            raise CapabilityRefusal("CAPABILITY_PATH_REJECTED", "The workspace read path is not granted.")
        max_bytes = arguments["maxBytes"]
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= MAX_IO_BYTES:
            raise CapabilityRefusal("CAPABILITY_INPUT_LIMIT", "The read byte limit is invalid.")
        path = self._workspace_path(arguments["path"], allow_missing_leaf=False)
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError as error:
            raise CapabilityRefusal("CAPABILITY_NOT_FOUND", "The requested workspace file does not exist.") from error
        except OSError as error:
            raise CapabilityRefusal("CAPABILITY_SYMLINK_REJECTED", "The requested workspace path is not a safe regular file.") from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise CapabilityRefusal("CAPABILITY_NOT_FILE", "The requested workspace path is not a regular file.")
            data = os.read(descriptor, max_bytes + 1)
        finally:
            os.close(descriptor)
        if len(data) > max_bytes:
            raise CapabilityRefusal("CAPABILITY_OUTPUT_LIMIT", "The workspace read exceeds its declared byte limit.")
        return {
            "bytesBase64": base64.b64encode(data).decode("ascii"),
            "byteLength": len(data),
        }

    def _write(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self._closed_arguments(arguments, {"path", "bytesBase64"})
        if arguments["path"] not in self.write_allowlist:
            raise CapabilityRefusal("CAPABILITY_PATH_REJECTED", "The workspace write path is not granted.")
        encoded = arguments["bytesBase64"]
        if not isinstance(encoded, str):
            raise CapabilityRefusal("CAPABILITY_INPUT_LIMIT", "The workspace write payload is invalid.")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise CapabilityRefusal("CAPABILITY_INPUT_LIMIT", "The workspace write payload is invalid.") from error
        if len(data) > MAX_IO_BYTES:
            raise CapabilityRefusal("CAPABILITY_INPUT_LIMIT", "The workspace write exceeds the fixture byte limit.")
        path = self._workspace_path(arguments["path"], allow_missing_leaf=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileNotFoundError as error:
            raise CapabilityRefusal("CAPABILITY_NOT_FOUND", "The workspace write parent does not exist.") from error
        except OSError as error:
            raise CapabilityRefusal("CAPABILITY_SYMLINK_REJECTED", "The workspace write target is not safe.") from error
        try:
            os.fchmod(descriptor, 0o600)
            cursor = 0
            while cursor < len(data):
                cursor += os.write(descriptor, data[cursor:])
        finally:
            os.close(descriptor)
        return {"bytesWritten": len(data)}

    def _exec(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self._closed_arguments(
            arguments,
            {"argv", "cwd", "env", "timeoutMs", "maxOutputBytes"},
        )
        argv = arguments["argv"]
        if not isinstance(argv, list) or not argv or len(argv) > 128 or not all(isinstance(value, str) for value in argv):
            raise CapabilityRefusal("CAPABILITY_EXECUTABLE_REJECTED", "The process argv is invalid.")
        if any("\0" in value or len(value) > 4096 for value in argv):
            raise CapabilityRefusal("CAPABILITY_EXECUTABLE_REJECTED", "The process argv is invalid.")
        executable = argv[0]
        if Path(executable).name.lower() in SHELL_NAMES:
            raise CapabilityRefusal("CAPABILITY_SHELL_REJECTED", "Shell executables are not permitted by the local bridge.")
        if executable not in self.executable_allowlist:
            raise CapabilityRefusal("CAPABILITY_EXECUTABLE_REJECTED", "The executable is not allowlisted by the local bridge.")
        env = arguments["env"]
        if not isinstance(env, Mapping) or any(
            name not in SAFE_ENVIRONMENT or not isinstance(value, str) or "\0" in value
            for name, value in env.items()
        ):
            raise CapabilityRefusal("CAPABILITY_ENV_REJECTED", "The process environment contains a non-allowlisted entry.")
        timeout_ms = arguments["timeoutMs"]
        output_limit = arguments["maxOutputBytes"]
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 1 <= timeout_ms <= 30000:
            raise CapabilityRefusal("CAPABILITY_INPUT_LIMIT", "The process timeout is invalid.")
        if not isinstance(output_limit, int) or isinstance(output_limit, bool) or not 1 <= output_limit <= MAX_IO_BYTES:
            raise CapabilityRefusal("CAPABILITY_INPUT_LIMIT", "The process output limit is invalid.")
        cwd = self._workspace_path(arguments["cwd"], allow_missing_leaf=False, allow_root=True)
        if not cwd.is_dir():
            raise CapabilityRefusal("CAPABILITY_PATH_REJECTED", "The process working directory is not a directory.")
        logical_argv = list(argv)
        executor_argv = (
            self.executable_allowlist[executable] + logical_argv[1:]
            if self._uses_default_executor
            else logical_argv
        )
        try:
            exit_code, stdout, stderr = self.executor(
                executor_argv,
                cwd,
                dict(env),
                timeout_ms,
                output_limit,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CapabilityRefusal("CAPABILITY_EXECUTION_FAILED", "The allowlisted process failed to execute.") from error
        if len(stdout) + len(stderr) > output_limit:
            raise CapabilityRefusal("CAPABILITY_OUTPUT_LIMIT", "The allowlisted process exceeded its output byte limit.")
        return {
            "exitCode": exit_code,
            "stdoutBase64": base64.b64encode(stdout).decode("ascii"),
            "stderrBase64": base64.b64encode(stderr).decode("ascii"),
        }

    def _workspace_path(
        self,
        raw: Any,
        *,
        allow_missing_leaf: bool,
        allow_root: bool = False,
    ) -> Path:
        if not isinstance(raw, str) or not raw or "\0" in raw or "\\" in raw:
            raise CapabilityRefusal("CAPABILITY_PATH_REJECTED", "The workspace path is invalid.")
        if raw == "." and allow_root:
            return self.workspace
        candidate = Path(raw)
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            raise CapabilityRefusal("CAPABILITY_PATH_REJECTED", "The workspace path escapes or aliases its root.")
        current = self.workspace
        for index, part in enumerate(candidate.parts):
            current = current / part
            is_leaf = index == len(candidate.parts) - 1
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                if is_leaf and allow_missing_leaf:
                    break
                raise CapabilityRefusal("CAPABILITY_NOT_FOUND", "The requested workspace path does not exist.")
            if stat.S_ISLNK(metadata.st_mode):
                raise CapabilityRefusal("CAPABILITY_SYMLINK_REJECTED", "Symlinks are not permitted in local capability paths.")
            if not is_leaf and not stat.S_ISDIR(metadata.st_mode):
                raise CapabilityRefusal("CAPABILITY_PATH_REJECTED", "A workspace path parent is not a directory.")
        try:
            current.relative_to(self.workspace)
        except ValueError as error:
            raise CapabilityRefusal("CAPABILITY_PATH_REJECTED", "The workspace path escapes its root.") from error
        return current

    @staticmethod
    def _closed_arguments(arguments: Mapping[str, Any], expected: set[str]) -> None:
        if set(arguments) != expected:
            raise CapabilityRefusal(
                "CAPABILITY_OPERATION_REJECTED",
                "The capability arguments have unknown or missing fields.",
            )

    @staticmethod
    def _response(
        request: Mapping[str, Any],
        response_type: str,
        **payload: Any,
    ) -> dict[str, Any]:
        return {
            "schema": WIRE_SCHEMA,
            "type": response_type,
            "requestId": str(request.get("requestId", "invalid-request")),
            "runId": str(request.get("runId", "invalid-run")),
            "correlationId": str(request.get("correlationId", "invalid-correlation")),
            **payload,
        }

    def _execute(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        timeout_ms: int,
        max_output_bytes: int,
    ) -> tuple[int, bytes, bytes]:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        streams = (process.stdout, process.stderr)
        buffers = (bytearray(), bytearray())
        lock = threading.Lock()
        output_limit_exceeded = threading.Event()
        reader_failed = threading.Event()
        retained = 0

        def read_bounded(index: int) -> None:
            nonlocal retained
            stream = streams[index]
            try:
                while True:
                    chunk = stream.read(8192)
                    if not chunk:
                        return
                    with lock:
                        remaining = max_output_bytes - retained
                        accepted = chunk[: max(0, remaining)]
                        buffers[index].extend(accepted)
                        retained += len(accepted)
                        self._max_retained_output_bytes_observed = max(
                            self._max_retained_output_bytes_observed,
                            retained,
                        )
                        overflow = len(chunk) > len(accepted)
                    if overflow:
                        output_limit_exceeded.set()
                        _kill_direct_child(process)
                        return
            except OSError:
                reader_failed.set()
                _kill_direct_child(process)

        readers = [
            threading.Thread(
                target=read_bounded,
                args=(index,),
                daemon=True,
                name=f"openprose-fixture-output-{index}",
            )
            for index in range(2)
        ]
        for reader in readers:
            reader.start()

        timed_out = False
        reap_failed = False
        try:
            process.wait(timeout=timeout_ms / 1000.0)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_direct_child(process)
        finally:
            if process.poll() is None:
                _kill_direct_child(process)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                reap_failed = True
            self._direct_child_reaped = not reap_failed
            for reader in readers:
                reader.join(timeout=5)
            for stream in streams:
                stream.close()

        if reap_failed:
            raise CapabilityRefusal(
                "CAPABILITY_EXECUTION_FAILED",
                "The direct allowlisted process could not be reaped.",
            )
        if any(reader.is_alive() for reader in readers) or reader_failed.is_set():
            raise CapabilityRefusal(
                "CAPABILITY_EXECUTION_FAILED",
                "The direct allowlisted process output reader did not settle.",
            )
        if output_limit_exceeded.is_set():
            raise CapabilityRefusal(
                "CAPABILITY_OUTPUT_LIMIT",
                "The allowlisted process exceeded its output byte limit.",
            )
        if timed_out:
            raise CapabilityRefusal(
                "CAPABILITY_EXECUTION_FAILED",
                "The allowlisted process exceeded its execution deadline.",
            )
        return process.returncode, bytes(buffers[0]), bytes(buffers[1])


def _kill_direct_child(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass
