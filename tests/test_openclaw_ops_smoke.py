from __future__ import annotations

import argparse
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from main_computer import openclaw_ops_smoke


class _OneShotServer:
    config: openclaw_ops_smoke.Config

    def __init__(self, server_address: tuple[str, int], handler_class: type[openclaw_ops_smoke.OpsHandler]) -> None:
        self.server_address = server_address
        self.handler_class = handler_class
        self.closed = False

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


class OpenClawOpsSmokeTests(unittest.TestCase):
    def test_default_ops_root_points_at_package_parent(self) -> None:
        root = openclaw_ops_smoke.default_ops_root()
        self.assertTrue((root / "main_computer" / "openclaw_ops_smoke.py").exists())

    def test_serve_uses_default_root_when_no_root_env_is_set(self) -> None:
        captured: dict[str, _OneShotServer] = {}

        def make_server(
            server_address: tuple[str, int],
            handler_class: type[openclaw_ops_smoke.OpsHandler],
        ) -> _OneShotServer:
            server = _OneShotServer(server_address, handler_class)
            captured["server"] = server
            return server

        args = argparse.Namespace(
            token="secret-token",
            root=None,
            max_read_bytes=123,
            host="127.0.0.1",
            port=0,
        )

        env = {key: value for key, value in os.environ.items() if key != "OPENCLAW_OPS_ROOT"}
        with patch.dict(os.environ, env, clear=True):
            with patch("main_computer.openclaw_ops_smoke.OpsServer", side_effect=make_server):
                with patch("builtins.print"):
                    openclaw_ops_smoke.serve(args)

        server = captured["server"]
        self.assertEqual(server.config.root, openclaw_ops_smoke.default_ops_root().resolve())
        self.assertEqual(server.config.max_read_bytes, 123)
        self.assertTrue(server.closed)

    def test_resolve_under_root_rejects_traversal(self) -> None:
        root = Path.cwd()
        with self.assertRaises(PermissionError):
            openclaw_ops_smoke.resolve_under_root(root, "../outside")


if __name__ == "__main__":
    unittest.main()
