"""MCEL v1 contract for navigable semantic destinations.

Identity, destination and presentation are distinct. A destination is a real,
shareable canonical URL, independent of whether the shell handles a primary
click in place. An address is not authorization to read or mutate the resource.
"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlsplit

SCHEMA = "mcel.navigation-destination.v1"
OPEN_MODES = ("same-tab", "new-tab", "new-window")
_ID = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_FIELD = re.compile(r"\{([A-Za-z][A-Za-z0-9]*)\}")
_VALID_PATH = re.compile(r"^/applications(?:/[A-Za-z0-9._~!$&'()+,;=:@%{}-]+)*$")


class NavigationContractError(ValueError):
    """A destination cannot be presented as a canonical navigable place."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise NavigationContractError(reason)


def validate_destination(destination: dict) -> dict:
    """Validate a declarative address, including an optional path template.

    A link is accessible through all browser-native opening modes.  For a
    template, `resolve_destination` must bind every segment before a link is
    made interactive. Query strings containing runtime/privilege state are not
    portable addresses; such state must stay in application/session storage.
    """
    _require(isinstance(destination, dict), "destination-not-object")
    _require(set(destination) == {"schema", "id", "href", "openModes", "access"},
             "destination-fields-invalid")
    _require(destination["schema"] == SCHEMA, "navigation-schema-invalid")
    _require(isinstance(destination["id"], str) and _ID.fullmatch(destination["id"]) is not None,
             "destination-id-invalid")
    _require(destination["access"] in ("public", "authenticated", "administrator"),
             "destination-access-invalid")
    _require(destination["openModes"] == list(OPEN_MODES), "destination-opening-modes-incomplete")
    href = destination["href"]
    _require(isinstance(href, str) and len(href) <= 2048 and not any(c.isspace() for c in href),
             "destination-href-invalid")
    parsed = urlsplit(href)
    _require(not parsed.scheme and not parsed.netloc and not parsed.fragment,
             "destination-not-local-canonical")
    _require(not parsed.query, "destination-query-not-canonical")
    path = parsed.path
    _require(bool(_VALID_PATH.fullmatch(path)) and "//" not in path and "\\" not in path,
             "destination-path-invalid")
    _require(all(segment not in (".", "..") for segment in unquote(path).split("/")),
             "destination-traversal-forbidden")
    _require("%2f" not in path.lower() and "%5c" not in path.lower(),
             "destination-encoded-separator-forbidden")
    fields = _FIELD.findall(path)
    _require(len(fields) == len(set(fields)) and path.count("{") == len(fields) and
             path.count("}") == len(fields), "destination-template-invalid")
    _require(all(re.fullmatch(r"\{[A-Za-z][A-Za-z0-9]*\}", segment) is not None
                 for segment in path.split("/") if "{" in segment or "}" in segment),
             "destination-template-invalid")
    return destination


def resolve_destination(destination: dict, params: dict[str, str] | None = None) -> str:
    """Return a canonical, browser-openable path only when fully bound."""
    validate_destination(destination)
    params = {} if params is None else params
    _require(isinstance(params, dict), "destination-parameters-invalid")
    fields = set(_FIELD.findall(destination["href"]))
    _require(set(params) == fields, "destination-parameters-mismatch")
    href = destination["href"]
    for name in fields:
        value = params[name]
        _require(isinstance(value, str) and bool(value) and len(value) <= 256 and
                 value not in (".", "..") and not any(c in value for c in "/\\?#\x00"),
                 "destination-parameter-unsafe")
        href = href.replace("{" + name + "}", quote(value, safe="-._~"))
    _require("{" not in href and "}" not in href, "destination-unresolved")
    return href


def make_destination(identifier: str, href: str, *, access: str = "public") -> dict:
    value = {"schema": SCHEMA, "id": identifier, "href": href,
             "openModes": list(OPEN_MODES), "access": access}
    validate_destination(value)
    return value
