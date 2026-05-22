from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class CommandError(RuntimeError):
    """Raised when a child process exits unsuccessfully or times out."""

    def __init__(self, message: str, *, result: "CommandResult") -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    cwd: str
    timeout_seconds: int | None
    exit_code: int
    output: str
    timed_out: bool = False


def quote_command(args: Sequence[str | os.PathLike[str]]) -> str:
    return subprocess.list2cmdline([os.fspath(arg) for arg in args])


def _reader_thread(stream, output_queue: "queue.Queue[str | None]") -> None:
    try:
        for line in iter(stream.readline, ""):
            output_queue.put(line)
    except ValueError:
        # The controller may close its read side after the parent process exits
        # while a detached descendant still has the inherited pipe open.
        pass
    finally:
        output_queue.put(None)


def run_command(
    args: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout_seconds: int | None = 300,
    env: Mapping[str, str] | None = None,
    log_path: str | os.PathLike[str] | None = None,
    check: bool = True,
) -> CommandResult:
    """Run a subprocess with live, line-oriented output streaming.

    The child receives an argument list rather than a shell string. stdout and
    stderr are merged so the console and the optional log preserve ordering.
    """

    argv = tuple(os.fspath(arg) for arg in args)
    working_directory = os.fspath(cwd or os.getcwd())
    timeout_display = "none" if timeout_seconds is None else str(timeout_seconds)

    print("Running command:", flush=True)
    print(f"  {quote_command(argv)}", flush=True)
    print("Working directory:", flush=True)
    print(f"  {working_directory}", flush=True)
    print(f"Timeout seconds: {timeout_display}", flush=True)

    log_handle = None
    if log_path is not None:
        log_file = Path(log_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        print(f"Log file: {log_file}", flush=True)
        log_handle = log_file.open("w", encoding="utf-8", errors="replace")

    output_parts: list[str] = []
    output_queue: "queue.Queue[str | None]" = queue.Queue()
    timed_out = False

    try:
        proc = subprocess.Popen(
            argv,
            cwd=working_directory,
            env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        reader = threading.Thread(target=_reader_thread, args=(proc.stdout, output_queue), daemon=True)
        reader.start()

        deadline = None if timeout_seconds is None else (time.monotonic() + timeout_seconds)
        reader_done = False
        process_exited_at: float | None = None
        last_output_at = time.monotonic()
        while True:
            try:
                item = output_queue.get(timeout=0.1)
                synthetic_empty = False
            except queue.Empty:
                item = None
                synthetic_empty = True

            if item is None and not synthetic_empty:
                reader_done = True
            elif item is not None:
                last_output_at = time.monotonic()
                output_parts.append(item)
                print(item, end="", flush=True)
                if log_handle is not None:
                    log_handle.write(item)
                    log_handle.flush()

            if deadline is not None and time.monotonic() > deadline and proc.poll() is None:
                timed_out = True
                proc.kill()
                break

            if proc.poll() is not None:
                if reader_done and output_queue.empty():
                    break

                now = time.monotonic()
                if process_exited_at is None:
                    process_exited_at = now

                # Long-running apps that are spawned by the command can inherit
                # the stdout pipe.  In that case the PowerShell/Python parent is
                # already done, but the reader thread never receives EOF and the
                # installer appears to hang after successful startup.  Once the
                # parent process has exited and the queue has been quiet briefly,
                # finish with the parent's exit code instead of waiting on
                # descendant processes to close inherited handles.
                if now - max(process_exited_at, last_output_at) >= 1.0:
                    break

        # Drain anything that arrived between process exit/kill and reader completion.
        while True:
            try:
                item = output_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                continue
            output_parts.append(item)
            print(item, end="", flush=True)
            if log_handle is not None:
                log_handle.write(item)

        if timed_out:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            exit_code = 124
        else:
            exit_code = proc.wait()
    finally:
        if log_handle is not None:
            log_handle.close()

    result = CommandResult(
        args=argv,
        cwd=working_directory,
        timeout_seconds=timeout_seconds,
        exit_code=exit_code,
        output="".join(output_parts),
        timed_out=timed_out,
    )

    if check and (timed_out or exit_code != 0):
        if timed_out:
            message = f"Command timed out after {timeout_seconds} seconds: {quote_command(argv)}"
        else:
            message = f"Command failed with exit code {exit_code}: {quote_command(argv)}"
        raise CommandError(message, result=result)

    return result
