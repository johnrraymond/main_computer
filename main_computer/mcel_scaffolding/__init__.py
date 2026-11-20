"""MCEL application scaffolding public surface for the repository-local generator."""

from .generator import (
    DEFAULT_TEMPLATE_VERSION,
    LEGACY_TEMPLATE_VERSION,
    SUPPORTED_TEMPLATE_VERSIONS,
    TARGET_GAPS,
    InvalidScaffoldInput,
    McelScaffoldingError,
    ScaffoldResult,
    ScaffoldValidationError,
    ScaffoldWriteError,
    UnsafeScaffoldDestination,
    generate_application,
    render_package_files,
    validate_app_id,
)
from .package_validator import PackageValidationResult, validate_package_files, validate_package_path

__all__ = [
    "DEFAULT_TEMPLATE_VERSION",
    "LEGACY_TEMPLATE_VERSION",
    "SUPPORTED_TEMPLATE_VERSIONS",
    "TARGET_GAPS",
    "InvalidScaffoldInput",
    "McelScaffoldingError",
    "PackageValidationResult",
    "ScaffoldResult",
    "ScaffoldValidationError",
    "ScaffoldWriteError",
    "UnsafeScaffoldDestination",
    "generate_application",
    "render_package_files",
    "validate_app_id",
    "validate_package_files",
    "validate_package_path",
]
