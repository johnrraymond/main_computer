from __future__ import annotations

from collections import defaultdict

from .errors import FdbControlError
from .models import CoordinatorEndpoint, ServicePlacement


def coordinator_from_service(service: ServicePlacement) -> CoordinatorEndpoint:
    return CoordinatorEndpoint(
        service_id=service.service_id,
        host_id=service.host_id,
        address=service.address,
        port=service.port,
    )


def resolve_explicit_coordinators(
    services: tuple[ServicePlacement, ...],
    coordinator_service_ids: tuple[str, ...],
) -> tuple[CoordinatorEndpoint, ...]:
    by_id = {service.service_id: service for service in services}
    if len(by_id) != len(services):
        raise ValueError("service ids must be unique")

    _validate_unique_endpoints(services)

    coordinators: list[CoordinatorEndpoint] = []
    for service_id in coordinator_service_ids:
        service = by_id.get(service_id)
        if service is None:
            raise ValueError(f"coordinator service {service_id!r} is not in the requested service set")
        coordinators.append(coordinator_from_service(service))
    return _sort_coordinators(coordinators)


def derive_topology_coordinators(
    services: tuple[ServicePlacement, ...],
    current: tuple[CoordinatorEndpoint, ...],
) -> tuple[CoordinatorEndpoint, ...]:
    """Derive the coordinator overlay from service failure domains.

    The target cardinality is the largest odd number that can be placed on
    distinct FDB zones.  Existing coordinators are retained when they are still
    valid and do not duplicate a selected failure domain; remaining slots are
    filled deterministically from the surviving service set.

    This makes coordinator membership a topology-derived overlay rather than an
    incidental side effect of service numbering or service count.  Multiple FDB
    services in one zone contribute only one coordinator candidate.
    """

    if not services:
        raise FdbControlError(
            code="FDB_COORDINATOR_TOPOLOGY_EMPTY",
            message="a live FDB topology requires at least one service from which to derive coordinators",
            module_id="FDB-OFM-COORD-001",
            retry_class="never",
        )
    by_id = {service.service_id: service for service in services}
    if len(by_id) != len(services):
        raise ValueError("service ids must be unique")
    _validate_unique_endpoints(services)

    by_zone: dict[str, list[ServicePlacement]] = defaultdict(list)
    for service in services:
        by_zone[service.zone_id].append(service)
    for members in by_zone.values():
        members.sort(key=lambda item: item.service_id.encode("utf-8"))

    zone_count = len(by_zone)
    target_count = zone_count if zone_count % 2 == 1 else zone_count - 1
    target_count = max(1, target_count)

    chosen: list[ServicePlacement] = []
    used_zones: set[str] = set()

    # Preserve valid current coordinators first.  Sorting makes the result
    # interpretation-stable even if the accepted wire order differs.
    for coordinator in sorted(current, key=lambda item: item.service_id.encode("utf-8")):
        service = by_id.get(coordinator.service_id)
        if service is None or service.zone_id in used_zones:
            continue
        chosen.append(service)
        used_zones.add(service.zone_id)
        if len(chosen) == target_count:
            break

    if len(chosen) < target_count:
        candidates = sorted(
            services,
            key=lambda item: (item.zone_id.encode("utf-8"), item.service_id.encode("utf-8")),
        )
        selected_ids = {item.service_id for item in chosen}
        for service in candidates:
            if service.service_id in selected_ids or service.zone_id in used_zones:
                continue
            chosen.append(service)
            selected_ids.add(service.service_id)
            used_zones.add(service.zone_id)
            if len(chosen) == target_count:
                break

    if len(chosen) != target_count:
        raise FdbControlError(
            code="FDB_COORDINATOR_TOPOLOGY_UNSATISFIED",
            message=(
                f"could not derive {target_count} coordinators across {zone_count} distinct FDB zones"
            ),
            module_id="FDB-OFM-COORD-001",
            retry_class="never",
        )
    return _sort_coordinators(coordinator_from_service(item) for item in chosen)


def coordinator_set_changed(
    before: tuple[CoordinatorEndpoint, ...],
    after: tuple[CoordinatorEndpoint, ...],
) -> bool:
    return _coordinator_signature(before) != _coordinator_signature(after)


def _coordinator_signature(values: tuple[CoordinatorEndpoint, ...]) -> tuple[tuple[str, str, str, int], ...]:
    return tuple(
        sorted(
            ((item.service_id, item.host_id, item.address, item.port) for item in values),
            key=lambda item: item[0].encode("utf-8"),
        )
    )


def _sort_coordinators(values) -> tuple[CoordinatorEndpoint, ...]:
    return tuple(sorted(tuple(values), key=lambda item: item.service_id.encode("utf-8")))


def _validate_unique_endpoints(services: tuple[ServicePlacement, ...]) -> None:
    endpoint_owners: dict[str, str] = {}
    for service in services:
        previous = endpoint_owners.setdefault(service.endpoint, service.service_id)
        if previous != service.service_id:
            raise ValueError(
                f"FDB endpoint {service.endpoint!r} is shared by services {previous!r} and {service.service_id!r}"
            )
