from __future__ import annotations

from main_computer.viewport_state import *  # noqa: F401,F403

class ViewportTerminalRoutesMixin:
    def _handle_terminal_run(self) -> None:
        try:
            body = self._read_json()
            command = str(body.get("command", "")).strip()
            if not command:
                self.server.signal("api-terminal-rejected", reason="empty-command")
                self._send_json({"error": "Command is required."}, status=HTTPStatus.BAD_REQUEST)
                return
            if len(command) > 4000:
                self.server.signal("api-terminal-rejected", reason="command-too-long")
                self._send_json({"error": "Command is limited to 4000 characters."}, status=HTTPStatus.BAD_REQUEST)
                return
            timeout_s = max(1.0, min(60.0, float(body.get("timeout_s", 15) or 15)))
            cwd = self._terminal_cwd(str(body.get("cwd", ".") or "."))
            started = time.monotonic()
            self.server.signal("api-terminal-start", cwd=cwd, timeout_s=timeout_s, command_chars=len(command))
            wrapped_command = self._terminal_wrapped_command(command)
            try:
                completed = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        wrapped_command,
                    ],
                    cwd=str(cwd),
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
                duration_ms = int((time.monotonic() - started) * 1000)
                stdout, final_cwd = self._terminal_extract_cwd(completed.stdout, cwd)
                self.server.signal(
                    "api-terminal-complete",
                    cwd=final_cwd,
                    exit_code=completed.returncode,
                    duration_ms=duration_ms,
                )
                self._send_json(
                    {
                        "command": command,
                        "cwd": str(final_cwd),
                        "exit_code": completed.returncode,
                        "stdout": stdout,
                        "stderr": completed.stderr,
                        "duration_ms": duration_ms,
                        "timed_out": False,
                    }
                )
            except subprocess.TimeoutExpired as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
                stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace")
                self.server.signal("api-terminal-timeout", cwd=cwd, duration_ms=duration_ms)
                self._send_json(
                    {
                        "command": command,
                        "cwd": str(cwd),
                        "final_cwd": str(cwd),
                        "exit_code": None,
                        "stdout": stdout,
                        "stderr": stderr,
                        "duration_ms": duration_ms,
                        "timed_out": True,
                        "error": f"Command timed out after {timeout_s:g} seconds.",
                    },
                    status=HTTPStatus.REQUEST_TIMEOUT,
                )
        except Exception as exc:
            self.server.signal("api-terminal-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_terminal_suggest(self) -> None:
        try:
            body = self._read_json()
            prompt = str(body.get("prompt", "") or "").strip()
            if not prompt:
                self.server.signal("api-terminal-suggest-rejected", reason="empty-prompt")
                self._send_json({"error": "Prompt is required."}, status=HTTPStatus.BAD_REQUEST)
                return
            cwd = self._terminal_cwd(str(body.get("cwd", ".") or "."))
            self.server.signal("api-terminal-suggest-start", cwd=cwd, prompt_chars=len(prompt))
            response = self.server.computer.suggest_terminal_command(prompt, cwd=str(cwd))
            try:
                suggestion = parse_terminal_suggestion(response.content)
                command = validate_terminal_command(str(suggestion.get("command", "")))
            except ValueError as exc:
                self.server.signal("api-terminal-suggest-invalid", error=exc)
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
                return
            risk = normalize_terminal_risk(str(suggestion.get("risk", "")))
            description = str(suggestion.get("description", "") or "").strip()
            self.server.signal(
                "api-terminal-suggest-complete",
                cwd=cwd,
                provider=response.provider,
                model=response.model,
                risk=risk,
                command_chars=len(command),
            )
            self._send_json(
                {
                    "command": command,
                    "cwd": str(cwd),
                    "description": description,
                    "risk": risk,
                    "provider": response.provider,
                    "model": response.model,
                }
            )
        except Exception as exc:
            self.server.signal("api-terminal-suggest-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _terminal_cwd(self, requested: str) -> Path:
        base = self.server.debug_root.resolve()
        guard_root = self.server.config.workspace.resolve()
        cleaned = requested.strip() or "."
        candidate = Path(cleaned)
        if not candidate.is_absolute():
            candidate = base / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(guard_root)
        except ValueError as exc:
            raise ValueError("Terminal working directory must stay inside the local workspace.") from exc
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError("Terminal working directory does not exist.")
        return resolved

    def _terminal_wrapped_command(self, command: str) -> str:
        return (
            "$__mc_exit = 0\n"
            "try {\n"
            "  & {\n"
            f"{command}\n"
            "  } | Out-String -Stream\n"
            "  if ($global:LASTEXITCODE -ne $null) { $__mc_exit = $global:LASTEXITCODE }\n"
            "} catch {\n"
            "  [Console]::Error.WriteLine($_.Exception.Message)\n"
            "  $__mc_exit = 1\n"
            "} finally {\n"
            "  Write-Output \"__MAIN_COMPUTER_CWD__$((Get-Location).Path)\"\n"
            "}\n"
            "exit $__mc_exit"
        )

    def _terminal_extract_cwd(self, stdout: str, fallback: Path) -> tuple[str, Path]:
        marker = "__MAIN_COMPUTER_CWD__"
        lines = stdout.splitlines()
        kept: list[str] = []
        final_cwd = fallback
        guard_root = self.server.config.workspace.resolve()
        for line in lines:
            if line.startswith(marker):
                candidate = Path(line[len(marker) :].strip()).resolve()
                try:
                    candidate.relative_to(guard_root)
                    if candidate.exists() and candidate.is_dir():
                        final_cwd = candidate
                except ValueError:
                    kept.append(f"terminal cwd outside workspace ignored: {candidate}")
                continue
            kept.append(line)
        cleaned = "\n".join(kept)
        if cleaned and stdout.endswith("\n"):
            cleaned += "\n"
        return cleaned, final_cwd
