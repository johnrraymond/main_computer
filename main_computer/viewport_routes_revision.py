from __future__ import annotations

from main_computer.viewport_state import *  # noqa: F401,F403

class ViewportRevisionRoutesMixin:
    def _handle_revision_snapshot(self) -> None:
        try:
            body = self._read_json()
            if not self._debug_passcode_ok(body):
                self._send_json({"error": "Debug passcode is required."}, status=HTTPStatus.FORBIDDEN)
                return
            label = str(body.get("label", "manual checkpoint"))
            asset_snapshot = self.server.debug_asset_revisions.create_snapshot(
                label=f"assets for {label}",
                reason="system-snapshot",
            )
            report = self.server.revisions.create_snapshot(
                label=label,
                reason="manual",
                metadata={"debug_asset_snapshot_id": asset_snapshot.get("created", {}).get("id", "")},
            )
            self.server.signal("api-revisions-snapshot", snapshot_id=report.get("created", {}).get("id", ""))
            self._send_json(report)
        except Exception as exc:
            self.server.signal("api-revisions-snapshot-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_revision_diff(self) -> None:
        try:
            body = self._read_json()
            snapshot_id = str(body.get("id", ""))
            path = str(body.get("path", ""))
            report = self.server.revisions.diff_snapshot(snapshot_id, path)
            self.server.signal("api-revisions-diff", snapshot_id=snapshot_id, path=path)
            self._send_json(report)
        except Exception as exc:
            self.server.signal("api-revisions-diff-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_revision_restore(self) -> None:
        try:
            body = self._read_json()
            if not self._debug_passcode_ok(body):
                self._send_json({"error": "Debug passcode is required."}, status=HTTPStatus.FORBIDDEN)
                return
            snapshot_id = str(body.get("id", ""))
            path = str(body.get("path", ""))
            self.server.revisions.create_snapshot(label=f"before restore {path}", reason="pre-restore")
            report = self.server.revisions.restore_file(snapshot_id, path)
            self.server.signal("api-revisions-restore", snapshot_id=snapshot_id, path=path)
            self._send_json(report)
        except Exception as exc:
            self.server.signal("api-revisions-restore-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_revision_restore_system(self) -> None:
        try:
            body = self._read_json()
            if not self._debug_passcode_ok(body):
                self._send_json({"error": "Debug passcode is required."}, status=HTTPStatus.FORBIDDEN)
                return
            snapshot_id = str(body.get("id", ""))
            pre_restore_assets = self.server.debug_asset_revisions.create_snapshot(
                label="assets before system restore",
                reason="pre-system-restore",
            )
            pre_restore_system = self.server.revisions.create_snapshot(
                label="before system restore",
                reason="pre-system-restore",
                metadata={"debug_asset_snapshot_id": pre_restore_assets.get("created", {}).get("id", "")},
            )
            report = self.server.revisions.restore_snapshot(snapshot_id)
            asset_snapshot_id = str(report.get("metadata", {}).get("debug_asset_snapshot_id", ""))
            asset_report = None
            if asset_snapshot_id:
                asset_report = self.server.debug_asset_revisions.restore(asset_snapshot_id)
            self.server.signal("api-revisions-restore-system", snapshot_id=snapshot_id, asset_snapshot_id=asset_snapshot_id)
            self._send_json(
                {
                    **report,
                    "pre_restore": pre_restore_system.get("created"),
                    "debug_assets": asset_report,
                    "assets": self._list_debug_assets(),
                }
            )
        except Exception as exc:
            self.server.signal("api-revisions-restore-system-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
