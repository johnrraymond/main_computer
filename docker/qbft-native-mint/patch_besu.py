#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
from pathlib import Path


def patch(root: Path, source_class: Path) -> None:
    precompile_dir = root / "evm" / "src" / "main" / "java" / "org" / "hyperledger" / "besu" / "evm" / "precompile"
    registry = precompile_dir / "MainnetPrecompiledContracts.java"
    interface = precompile_dir / "PrecompiledContract.java"
    if not registry.exists() or not interface.exists():
        raise SystemExit("Unsupported Besu source tree: precompile sources not found")

    interface_text = interface.read_text(encoding="utf-8")
    if "MessageFrame messageFrame" not in interface_text:
        raise SystemExit("Unsupported Besu source tree: PrecompiledContract has no MessageFrame execution context")

    target_class = precompile_dir / "NativeMintPrecompiledContract.java"
    shutil.copy2(source_class, target_class)

    text = registry.read_text(encoding="utf-8")
    registration = (
        "registry.put(NativeMintPrecompiledContract.ADDRESS, "
        "new NativeMintPrecompiledContract());"
    )
    if registration not in text:
        needle = "registry.put(Address.ID, new IDPrecompiledContract(gasCalculator));"
        if needle not in text:
            raise SystemExit("Unsupported Besu source tree: frontier precompile registration anchor not found")
        text = text.replace(needle, needle + "\n    " + registration, 1)
        registry.write_text(text, encoding="utf-8")

    print(f"NATIVE_MINT_BESU_PATCH_OK root={root}")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: patch_besu.py BESU_SOURCE_ROOT NativeMintPrecompiledContract.java", file=sys.stderr)
        return 2
    patch(Path(argv[1]).resolve(), Path(argv[2]).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
