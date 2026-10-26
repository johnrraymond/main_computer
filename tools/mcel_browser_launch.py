"""Portable, fail-closed Playwright Chromium-family browser discovery.

Playwright-managed Chromium, Microsoft Edge and Google Chrome are all supported
on Windows without putting browser executables on PATH. A caller-specified
executable takes precedence and is never silently substituted. This launcher
never downloads software or relaxes browser acceptance checks.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import Any


class BrowserLaunchError(RuntimeError):
    """No usable locally installed Chromium-family browser could be launched."""


def _windows_candidates(environ: dict[str, str]) -> list[tuple[str, str]]:
    locations: list[tuple[str, str]] = []
    for key, tails in (
        ('PROGRAMFILES', ('Microsoft Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe')),
        ('PROGRAMFILES(X86)', ('Microsoft Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe')),
        ('LOCALAPPDATA', ('Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe')),
    ):
        if root := environ.get(key):
            for suffix in tails:
                candidate = Path(root).joinpath(*suffix.split('/'))
                if candidate.is_file():
                    locations.append((f'{key}/{suffix}', str(candidate)))
    return locations


def launch_chromium(
    playwright: Any, *, executable: str | os.PathLike[str] | None = None,
    headless: bool = True, args: list[str] | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[Any, dict[str, str]]:
    """Return ``(browser, launch provenance)`` or raise with installation advice.

    Precedence: explicit argument / MCEL_CHROMIUM_EXECUTABLE; Playwright browser;
    installed Edge, Chrome; executables discovered on PATH / known Windows roots.
    Neither Playwright's bundled browser nor Edge/Chrome require shell PATH entries.
    """
    environ = dict(os.environ if environ is None else environ)
    launch_args = {'headless': headless, 'args': list(args or [])}
    selected = str(executable) if executable is not None else environ.get('MCEL_CHROMIUM_EXECUTABLE')
    if selected:
        # An explicit path is authoritative: a typo must not silently switch engines.
        path = Path(os.path.expandvars(os.path.expanduser(selected)))
        if not path.is_file():
            raise BrowserLaunchError(f'Configured Chromium executable does not exist: {selected!r}. '
                                     'Fix --chromium-executable or MCEL_CHROMIUM_EXECUTABLE.')
        try:
            browser = playwright.chromium.launch(executable_path=str(path), **launch_args)
        except Exception as exc:
            raise BrowserLaunchError(f'Configured Chromium executable failed to launch: {path}: '
                                     f'{type(exc).__name__}: {str(exc)[:350]}') from exc
        return browser, {'method': 'explicit-executable' if executable is not None else 'environment-executable',
                         'value': str(path), 'browser_version': browser.version}

    attempts: list[tuple[str, dict[str, str]]] = [
        ('playwright-chromium', {}),
        ('installed-msedge', {'channel': 'msedge'}),
        ('installed-chrome', {'channel': 'chrome'}),
    ]
    for command in ('chromium', 'chromium-browser', 'msedge', 'microsoft-edge', 'chrome', 'google-chrome'):
        resolved = shutil.which(command)
        if resolved:
            attempts.append((f'path:{command}', {'executable_path': resolved}))
    attempts.extend((name, {'executable_path': path}) for name, path in _windows_candidates(environ))

    errors: list[str] = []
    already = set()
    for name, kwargs in attempts:
        marker = (kwargs.get('channel'), kwargs.get('executable_path'))
        if marker in already:
            continue
        already.add(marker)
        try:
            browser = playwright.chromium.launch(**kwargs, **launch_args)
            return browser, {'method': name, 'value': kwargs.get('executable_path') or kwargs.get('channel') or 'bundled',
                             'browser_version': browser.version}
        except Exception as exc:
            errors.append(f'{name}: {type(exc).__name__}: {str(exc).splitlines()[0][:140]}')

    raise BrowserLaunchError('No Chromium-family browser could be launched. '
                             'On Windows, Playwright can use installed Edge/Chrome without PATH. '
                             'Otherwise install the Playwright browser with: '
                             'python -m playwright install chromium. '
                             'Or set MCEL_CHROMIUM_EXECUTABLE to the full path of msedge.exe/chrome.exe. '
                             'Attempts: ' + ' | '.join(errors))
