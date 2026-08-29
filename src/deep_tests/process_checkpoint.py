"""Deterministic PTY checkpoints for black-box process interruption tests.

The driver waits for an application-owned semantic prompt instead of sleeping.
Prompts before the requested checkpoint are explicitly accepted. At the
checkpoint the caller can accept, decline, send terminal EOF, or forcefully
terminate the process. Every wait is bounded by a monotonic deadline.
"""

from __future__ import annotations

import errno
import os
import re
import select
import signal
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


PROMPT = re.compile(rb"interactive: (?P<step>[^\r\n]*?)\? \[y/N\] ")


class CheckpointAction(str, Enum):
    ACCEPT = "accept"
    DECLINE = "decline"
    EOF = "eof"
    KILL = "kill"


class CheckpointNotReached(RuntimeError):
    """Raised when the child exits without publishing the requested marker."""


@dataclass(frozen=True)
class CheckpointResult:
    command: tuple[str, ...]
    checkpoint: str
    action: CheckpointAction
    returncode: int
    output: str
    prompts: tuple[str, ...]
    killed: bool


def _read_available(master: int, output: bytearray) -> bool:
    """Read one available PTY frame; return false after terminal EOF."""

    try:
        frame = os.read(master, 65_536)
    except OSError as error:
        if error.errno == errno.EIO:
            return False
        raise
    if not frame:
        return False
    output.extend(frame)
    return True


def _terminate(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_at_checkpoint(
    command: Sequence[str],
    checkpoint: str,
    action: CheckpointAction,
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float = 120.0,
    before_action: Callable[[], None] | None = None,
) -> CheckpointResult:
    """Run ``command`` in a real PTY and act at one semantic prompt.

    ``before_action`` is useful when the process supervised by the local child
    lives elsewhere. For example, an OCI test kills the container first and
    only then terminates the attached ``docker run`` client.
    """

    if not command:
        raise ValueError("command must not be empty")
    if not checkpoint:
        raise ValueError("checkpoint must not be empty")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if not hasattr(os, "forkpty"):
        raise RuntimeError("PTY checkpoint tests require a Unix host")

    normalized = tuple(str(part) for part in command)
    pid, master = os.forkpty()
    if pid == 0:
        try:
            os.chdir(cwd)
            os.execvpe(normalized[0], list(normalized), dict(env))
        except BaseException as error:  # pragma: no cover - child-only failure path
            message = f"checkpoint child exec failed: {type(error).__name__}: {error}\n"
            os.write(2, message.encode("utf-8", errors="replace"))
            os._exit(127)

    deadline = time.monotonic() + timeout
    output = bytearray()
    prompt_cursor = 0
    prompts: list[str] = []
    target_seen = False
    killed = False
    status: int | None = None

    try:
        while status is None:
            while True:
                match = PROMPT.search(output, prompt_cursor)
                if match is None:
                    break
                prompt_cursor = match.end()
                step = match.group("step").decode("utf-8", errors="replace")
                prompts.append(step)

                is_target = not target_seen and checkpoint in step
                if is_target:
                    target_seen = True
                    if before_action is not None:
                        try:
                            before_action()
                        except BaseException:
                            _terminate(pid)
                            os.waitpid(pid, 0)
                            raise
                    if action is CheckpointAction.ACCEPT:
                        os.write(master, b"yes\n")
                    elif action is CheckpointAction.DECLINE:
                        os.write(master, b"no\n")
                    elif action is CheckpointAction.EOF:
                        # VEOF in canonical terminal mode makes read_line see EOF.
                        os.write(master, b"\x04")
                    else:
                        killed = True
                        _terminate(pid)
                elif not target_seen or action is CheckpointAction.ACCEPT:
                    os.write(master, b"yes\n")

            waited_pid, waited_status = os.waitpid(pid, os.WNOHANG)
            if waited_pid == pid:
                status = waited_status
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate(pid)
                _, status = os.waitpid(pid, 0)
                rendered = output.decode("utf-8", errors="replace")
                raise TimeoutError(
                    f"checkpoint process exceeded {timeout:.1f}s waiting for {checkpoint!r}; "
                    f"output tail={rendered[-2_000:]!r}"
                )

            readable, _, _ = select.select([master], [], [], min(0.25, remaining))
            if readable:
                _read_available(master, output)

        # Drain frames already queued when the child changed state.
        while True:
            readable, _, _ = select.select([master], [], [], 0)
            if not readable or not _read_available(master, output):
                break
    finally:
        os.close(master)

    rendered = output.decode("utf-8", errors="replace")
    if not target_seen:
        raise CheckpointNotReached(
            f"process exited before checkpoint {checkpoint!r}; "
            f"returncode={os.waitstatus_to_exitcode(status)}; "
            f"output tail={rendered[-2_000:]!r}"
        )
    return CheckpointResult(
        command=normalized,
        checkpoint=checkpoint,
        action=action,
        returncode=os.waitstatus_to_exitcode(status),
        output=rendered,
        prompts=tuple(prompts),
        killed=killed,
    )
