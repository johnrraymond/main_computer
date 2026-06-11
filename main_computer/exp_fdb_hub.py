from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from main_computer.config import MainComputerConfig
from main_computer.energy import EnergyCreditLedger
from main_computer.exp_fdb_credit_ledger import ExperimentalFoundationDbConfig, ExperimentalFoundationDbCreditLedger
from main_computer.hub import (
    DEFAULT_HUB_PORT,
    HUB_SECURITY_PROFILE,
    HubDispatcher,
    HubHttpServer,
    HubRegistry,
)
from main_computer.hub_credit_bridge_completion import HubCreditBridgeCompletionService
from main_computer.hub_credit_indexer import HubCreditIndexer


DEFAULT_EXP_FDB_HUB_PORT = DEFAULT_HUB_PORT + 100
DEFAULT_EXP_FDB_NAMESPACE = "main-computer-exp-fdb"
DEFAULT_EXP_FDB_CLUSTER_FILE = Path(".foundationdb") / "docker.cluster"
DEFAULT_EXP_FDB_HUB_ROOT = Path("runtime") / "exp-fdb-hub"


class ExperimentalFoundationDbHubHttpServer(HubHttpServer):
    """Manual-only Hub clone that swaps the credit ledger to FoundationDB."""

    def __init__(
        self,
        server_address: tuple[str, int],
        config: MainComputerConfig,
        *,
        fdb_config: ExperimentalFoundationDbConfig,
        verbose: bool = True,
    ) -> None:
        super().__init__(server_address, config, verbose=verbose)
        self.credit_ledger = ExperimentalFoundationDbCreditLedger(fdb_config)
        self.credit_indexer = HubCreditIndexer(self.credit_ledger)
        self.credit_bridge_completion = HubCreditBridgeCompletionService(self.credit_ledger, config)
        self.dispatcher = HubDispatcher(
            self.registry,
            self.energy_ledger,
            timeout_s=config.hub_timeout_s,
            allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
            credit_ledger=self.credit_ledger,
            default_credits_per_request=config.hub_credits_per_request,
        )


def build_experimental_config(args: argparse.Namespace) -> tuple[MainComputerConfig, ExperimentalFoundationDbConfig]:
    base = MainComputerConfig.from_env()
    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd().resolve()
    hub_root = Path(args.hub_root) if args.hub_root else DEFAULT_EXP_FDB_HUB_ROOT
    if not hub_root.is_absolute():
        hub_root = repo_root / hub_root

    hub_url = args.hub_url or f"http://{args.host}:{args.port}"
    config = replace(
        base,
        hub_root=hub_root,
        hub_bind_host=args.host,
        hub_bind_port=args.port,
        hub_url=hub_url,
        hub_network="exp-fdb",
        hub_network_display_name="Experimental FDB Hub",
        hub_network_kind="experimental",
        hub_allow_insecure_dev_network=True,
    )

    cluster_file = Path(args.cluster_file)
    if not cluster_file.is_absolute():
        cluster_file = repo_root / cluster_file

    fdb_config = ExperimentalFoundationDbConfig(
        cluster_file=cluster_file,
        namespace=args.namespace,
        api_version=args.api_version,
        repo_root=repo_root,
        activate_native_client=not args.no_activate_cached_native_client,
    )
    return config, fdb_config


def serve_exp_fdb_hub(args: argparse.Namespace) -> None:
    config, fdb_config = build_experimental_config(args)
    if not fdb_config.cluster_file.exists():
        raise SystemExit(
            f"FoundationDB cluster file not found: {fdb_config.cluster_file}\n"
            "Start the local FDB container first, for example:\n"
            "  python scripts/smoke_foundationdb_credit_ledger_primitives.py --keep-container"
        )

    server = ExperimentalFoundationDbHubHttpServer(
        (args.host, args.port),
        config,
        fdb_config=fdb_config,
        verbose=not args.noverbose,
    )
    fdb_health = server.credit_ledger.health_check()

    print(f"Experimental FDB hub server: http://{args.host}:{server.server_port}")
    print("Manual-only: this entry point is not part of normal Main Computer startup.")
    print(f"Hub runtime: {server.hub_root}")
    print(f"Hub admin/control site: http://{args.host}:{server.server_port}/admin")
    print(f"Hub security: high-security={config.hub_high_security} profile={HUB_SECURITY_PROFILE}; local experimental mode allows insecure dev network")
    print(f"FDB cluster file: {fdb_config.cluster_file}")
    print(f"FDB namespace: {fdb_config.namespace}")
    print(f"FDB health: {fdb_health}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nExperimental FDB hub stopped.")
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exp-fdb-hub.py",
        description=(
            "Start a manual-only clone of the Main Computer hub that uses the local "
            "FoundationDB Docker cluster for the compute-credit ledger."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host/interface to bind.")
    parser.add_argument("--port", type=int, default=DEFAULT_EXP_FDB_HUB_PORT, help="Port to bind. Defaults to 8870.")
    parser.add_argument("--hub-url", help="Public URL advertised for this experimental hub.")
    parser.add_argument("--hub-root", type=Path, default=DEFAULT_EXP_FDB_HUB_ROOT, help="Separate runtime root for the experimental hub.")
    parser.add_argument("--cluster-file", type=Path, default=DEFAULT_EXP_FDB_CLUSTER_FILE, help="FoundationDB cluster file written by the FDB smoke.")
    parser.add_argument("--namespace", default=DEFAULT_EXP_FDB_NAMESPACE, help="FDB tuple namespace for this experiment.")
    parser.add_argument("--api-version", type=int, default=740, help="FoundationDB API version to request.")
    parser.add_argument("--repo-root", type=Path, help="Repository root. Defaults to the current working directory.")
    parser.add_argument(
        "--no-activate-cached-native-client",
        action="store_true",
        help="Do not add .foundationdb/native-client to PATH/DLL search path before importing FDB.",
    )
    parser.add_argument(
        "-noverbose",
        "--noverbose",
        action="store_true",
        help="Suppress hub request logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    serve_exp_fdb_hub(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
