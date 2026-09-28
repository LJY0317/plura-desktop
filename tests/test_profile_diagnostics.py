"""Tests for privacy-safe official desktop session diagnostics."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from plura_desktop import profile_diagnostics as module


class ProfileDiagnosticTests(unittest.TestCase):
    def test_help_and_version_are_available_before_macos_runtime_guard(self):
        with patch.object(module.sys, "platform", "linux"):
            output = io.StringIO()
            with self.assertRaises(SystemExit) as help_exit, redirect_stdout(output):
                module.main(["--help"])
            self.assertEqual(help_exit.exception.code, 0)
            self.assertIn("--write-artifact", output.getvalue())

            output = io.StringIO()
            with self.assertRaises(SystemExit) as version_exit, redirect_stdout(output):
                module.main(["--version"])
            self.assertEqual(version_exit.exception.code, 0)
            self.assertIn("Plura Desktop diagnostics", output.getvalue())

    def test_non_macos_diagnostic_execution_fails_without_guessing_log_paths(self):
        with patch.object(module.sys, "platform", "linux"):
            error = io.StringIO()
            with redirect_stderr(error):
                result = module.main(["--profile", "2"])
            self.assertEqual(result, 2)
            self.assertIn("macOS-only", error.getvalue())

    @staticmethod
    def _install_artifact_fixture(home: Path, profile: int = 2) -> Path:
        metadata = home / "Library/Application Support/PluraDesktop"
        metadata.mkdir(parents=True)
        (metadata / f"profile-{profile}-install-manifest.json").write_text(
            '{"schema": 5, "id": "local.plura-desktop.profile%d", '
            '"profile_index": %d, "ready": true}\n' % (profile, profile),
            encoding="utf-8",
        )
        return metadata

    def test_bounded_diagnostic_artifact_uses_product_profile_incident_layout_and_retention(self):
        with tempfile.TemporaryDirectory(prefix="profile-diagnostic-artifact-", dir=ROOT) as root:
            home = Path(root)
            self._install_artifact_fixture(home)
            ids = [
                "20260928T120000Z-00000001",
                "20260928T120001Z-00000002",
                "20260928T120002Z-00000003",
            ]
            for incident in ids:
                result = module.write_diagnostic_artifact(
                    home,
                    2,
                    {"profile": 2, "window": {"parsedLineCount": 0}},
                    incident_id=incident,
                    max_incidents=2,
                )
                self.assertEqual(result["incident"], incident)
                self.assertLessEqual(result["sizeBytes"], module.DIAGNOSTIC_ARTIFACT_MAX_BYTES)

            incidents = module.diagnostics_root(home, 2)
            retained = sorted(path.name for path in incidents.iterdir())
            self.assertEqual(retained, ids[1:])
            summary = incidents / ids[-1] / "summary.json"
            self.assertTrue(summary.is_file())
            rendered = summary.read_text(encoding="utf-8")
            self.assertIn('"schema": 1', rendered)
            self.assertIn('"retentionMaxIncidents": 2', rendered)

    def test_diagnostic_artifact_size_cap_fails_before_creating_storage(self):
        with tempfile.TemporaryDirectory(prefix="profile-diagnostic-artifact-cap-", dir=ROOT) as root:
            home = Path(root)
            self._install_artifact_fixture(home)
            with self.assertRaisesRegex(RuntimeError, "exceeds bounded artifact size"):
                module.write_diagnostic_artifact(
                    home,
                    2,
                    {"oversized": "x" * 4096},
                    incident_id="20260928T120000Z-aaaaaaaa",
                    max_bytes=1024,
                )
            self.assertFalse(module.diagnostics_root(home, 2).exists())

    def test_diagnostic_retention_refuses_replaced_incident_directory(self):
        with tempfile.TemporaryDirectory(prefix="profile-diagnostic-artifact-link-", dir=ROOT) as root:
            home = Path(root)
            self._install_artifact_fixture(home)
            incidents = module.diagnostics_root(home, 2)
            incidents.mkdir(parents=True)
            target = home / "outside"
            target.mkdir()
            replaced = incidents / "20260928T120000Z-00000001"
            try:
                replaced.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable on this runner")
            with self.assertRaisesRegex(RuntimeError, "unsafe or replaced"):
                module.write_diagnostic_artifact(
                    home,
                    2,
                    {"profile": 2},
                    incident_id="20260928T120001Z-00000002",
                )
            self.assertFalse((incidents / "20260928T120001Z-00000002").exists())

    def test_diagnostic_retention_refuses_unknown_incident_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="profile-diagnostic-artifact-unknown-", dir=ROOT) as root:
            home = Path(root)
            self._install_artifact_fixture(home)
            incidents = module.diagnostics_root(home, 2)
            incidents.mkdir(parents=True)
            (incidents / "unexpected.txt").write_text("preserve me", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unexpected artifact"):
                module.write_diagnostic_artifact(
                    home,
                    2,
                    {"profile": 2},
                    incident_id="20260928T120001Z-00000002",
                )
            self.assertEqual((incidents / "unexpected.txt").read_text(), "preserve me")
            self.assertFalse((incidents / "20260928T120001Z-00000002").exists())

    def test_default_profile_cannot_create_persistent_product_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="profile-diagnostic-default-", dir=ROOT) as root:
            with self.assertRaisesRegex(ValueError, "Managed profile"):
                module.write_diagnostic_artifact(
                    Path(root),
                    1,
                    {"profile": 1},
                    incident_id="20260928T120000Z-aaaaaaaa",
                )

    def test_uninstalled_profile_cannot_create_orphan_diagnostic_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="profile-diagnostic-uninstalled-", dir=ROOT) as root:
            home = Path(root)
            metadata = home / "Library/Application Support/PluraDesktop"
            metadata.mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, "manifest"):
                module.write_diagnostic_artifact(
                    home,
                    2,
                    {"profile": 2},
                    incident_id="20260928T120000Z-aaaaaaaa",
                )
            self.assertFalse(module.diagnostics_root(home, 2).exists())

    def test_access_refetch_overlap_requires_both_signals_and_is_not_named_as_cause(self):
        cid = "6aaf922d-5b38-83ee-8eea-62e9a3c00780"
        lines = [
            "2026-09-20T10:15:06.625Z info [install-primary-runtime] "
            "primary_runtime_dependencies_diagnose_finished bundleVersion=26.909.11814 "
            "installed=true problemCount=0",
            "2026-09-20T10:15:07.000Z info [AppServerConnection] "
            "mcp_server_startup_status_updated error=null failureReason=null hostId=local "
            "server=node_repl status=ready threadId=01a0b9e2-a2e8-7d20-9cb4-37dbcbbe2eb6",
            "2026-09-20T10:15:08.000Z warning [electron-message-handler] "
            f'sa_server_request_failed errorCode=conversation_inaccessible '
            f'errorMessage=\"{{\\\"conversation_id\\\":\\\"{cid}\\\"}}\" '
            "method=post routePattern=/conversation/init status=404",
            "2026-09-20T10:15:09.000Z info [electron-message-handler] "
            f"chatgpt_conversation_refetch_started conversationId={cid} "
            "reason=explicit_update statusBefore=streaming",
            "2026-09-20T10:15:10.000Z info [electron-message-handler] "
            f"chatgpt_conversation_refetch_completed asyncStatus=null conversationId={cid} "
            "currentNodeApplied=false mappingSize=983 reason=explicit_update "
            "statusAfter=streaming statusBefore=streaming",
        ]
        summary = module.summarize_lines(
            lines, cutoff=datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(summary["primaryRuntime"]["last"]["installed"], True)
        self.assertEqual(summary["primaryRuntime"]["last"]["problemCount"], 0)
        self.assertEqual(summary["mcpStartup"]["lastByServer"]["node_repl"]["status"], "ready")
        self.assertEqual(len(summary["conversationAccessRefetchOverlaps"]), 1)
        suspect = summary["conversationAccessRefetchOverlaps"][0]
        self.assertEqual(suspect["reason"], "conversation_access_denied_while_refetch_completed")
        self.assertNotEqual(suspect["conversation"], cid)
        self.assertEqual(len(suspect["conversation"]), 12)

    def test_access_denial_without_refetch_is_not_promoted_to_overlap(self):
        cid = "6aaf922d-5b38-83ee-8eea-62e9a3c00780"
        summary = module.summarize_lines([
            "2026-09-20T10:15:08.000Z warning [electron-message-handler] "
            f"sa_server_request_failed errorCode=conversation_inaccessible "
            f"url=/conversation/{cid} status=404"
        ])
        self.assertEqual(len(summary["conversations"]), 1)
        self.assertEqual(summary["conversationAccessRefetchOverlaps"], [])

    def test_output_never_retains_raw_conversation_or_thread_id(self):
        cid = "6aaf922d-5b38-83ee-8eea-62e9a3c00780"
        tid = "01a0b9e2-a2e8-7d20-9cb4-37dbcbbe2eb6"
        summary = module.summarize_lines([
            "2026-09-20T10:15:07.000Z info [AppServerConnection] "
            f"mcp_server_startup_status_updated server=codex_app status=ready threadId={tid}",
            "2026-09-20T10:15:08.000Z warning [electron-message-handler] "
            f"sa_server_request_failed errorCode=conversation_inaccessible url=/conversation/{cid} status=404",
        ])
        rendered = str(summary)
        self.assertNotIn(cid, rendered)
        self.assertNotIn(tid, rendered)

    def test_project_route_and_workspace_dependency_evidence_stays_structural(self):
        cid = "6aaf922d-5b38-83ee-8eea-62e9a3c00780"
        project = "g-p-6a99ab6f33f48191b1292d466b6b81e4"
        lines = [
            "2026-09-20T10:15:01.000Z info [electron-message-handler] "
            f"IAB_LIFECYCLE received browser sidebar owner sync conversationId={cid} "
            f"ownerRoutePath=/g/{project}/c/{cid}",
            "2026-09-20T10:15:02.000Z info [electron-message-handler] "
            f"IAB_LIFECYCLE received browser sidebar owner sync conversationId={cid} "
            f"ownerRoutePath=/g/{project}-human-readable-slug/c/{cid}",
            "2026-09-20T10:15:03.000Z info [workspace-dependencies-tool] "
            "primary_runtime_dependency_tool_outcome outcome=available "
            "bundleVersion=26.909.11814 durationMs=4",
            "2026-09-20T10:15:04.000Z warning [install-primary-runtime] "
            "primary_runtime_restore_failed",
        ]
        summary = module.summarize_lines(lines)
        row = summary["conversations"][0]
        self.assertEqual(row["projectRouteVariantCount"], 2)
        self.assertNotEqual(row["projectBase"], project)
        self.assertEqual(len(row["projectBase"]), 12)
        self.assertEqual(summary["workspaceDependencies"]["toolOutcomes"], {"available": 1})
        self.assertEqual(summary["workspaceDependencies"]["lastToolOutcome"]["bundleVersion"], "26.909.11814")
        self.assertEqual(summary["workspaceDependencies"]["restoreFailureCount"], 1)
        self.assertNotIn(project, str(summary))

    def test_owner_route_resolves_server_conversation_when_field_is_client_placeholder(self):
        cid = "6aaf922d-5b38-83ee-8eea-62e9a3c00780"
        project = "g-p-6a99ab6f33f48191b1292d466b6b81e4"
        summary = module.summarize_lines([
            "2026-09-20T10:15:01.000Z info [electron-message-handler] "
            "IAB_LIFECYCLE received browser sidebar owner sync "
            "conversationId=client-new-thread:8ae737e7-98f7-456a-a812-26bb26935490 "
            f"ownerRoutePath=/g/{project}/c/{cid}",
        ])
        self.assertEqual(len(summary["conversations"]), 1)
        row = summary["conversations"][0]
        self.assertEqual(row["conversation"], module.opaque_id(cid))
        self.assertEqual(row["projectBase"], module.opaque_id(project))

    def test_native_codex_structural_log_reads_only_allowlisted_targets(self):
        with tempfile.TemporaryDirectory(prefix="profile-diagnostic-db-", dir=ROOT) as root:
            home = Path(root)
            codex = home / ".codex-profile2"
            codex.mkdir()
            database = codex / "logs_2.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE logs (ts INTEGER, ts_nanos INTEGER, level TEXT, target TEXT, "
                "feedback_log_body TEXT, thread_id TEXT, process_uuid TEXT)"
            )
            tid = "01a0b9e2-a2e8-7d20-9cb4-37dbcbbe2eb6"
            other_tid = "01a0be5b-71cb-7713-9f5f-7a52f057db99"
            process = "proc-fixture-a"
            rows = [
                (
                    1_789_900_000, 1, "INFO",
                    "codex_mcp::connection_manager::tool_catalog",
                    "list_tools_with_errors mcp_server_count=4 available_server_count=4 "
                    "unavailable_server_count=0 tool_count=309 built MCP tool list",
                    None, process,
                ),
                (
                    1_789_900_010, 2, "INFO",
                    "codex_mcp::connection_manager::tool_catalog",
                    "list_tools_with_errors mcp_server_count=4 available_server_count=3 "
                    "unavailable_server_count=1 tool_count=267 built MCP tool list",
                    None, process,
                ),
                (
                    1_789_900_020, 3, "INFO",
                    "codex_core::tools::spec_plan",
                    "append_dynamic_tool_runtimes dynamic_tool_count=11 "
                    "tool_names=SECRET_TOOL_NAMES args=SECRET_ARGS",
                    tid, process,
                ),
                (
                    1_789_900_021, 31, "TRACE",
                    "codex_core::tools::spec_plan",
                    "build_specs tool_spec_count=23 cwd=/SECRET/TOOL/PLAN/PATH",
                    tid, process,
                ),
                (
                    1_789_900_022, 32, "TRACE",
                    "codex_core::tools::spec_plan",
                    "append_dynamic_tool_runtimes dynamic_tool_count=7 "
                    "tool_spec_count=19 arbitrary_private_name=SECRET_PRIVATE_NAME",
                    tid, process,
                ),
                (
                    1_789_900_030, 4, "INFO",
                    "rmcp::service",
                    "server_name=node_repl dynamic_tool_count=7 new Service initialized as client "
                    "request_id=SECRET_REQUEST connection_id=SECRET_CONNECTION",
                    None, process,
                ),
                (
                    1_789_900_040, 5, "WARN",
                    "rmcp::service",
                    "server_name=private_customer_connector dynamic_tool_count=5 new task cancelled "
                    "peer_info=SECRET_PEER",
                    None, process,
                ),
                (
                    1_789_900_050, 6, "DEBUG",
                    "codex_app_server::request_processors::thread_lifecycle",
                    "composing running thread resume response "
                    f"thread_id={tid} "
                    "active_turn_present=true active_turn_status=inProgress",
                    tid, process,
                ),
                (
                    1_789_900_060, 7, "DEBUG",
                    "codex_app_server::thread_state",
                    "clearing thread listener during thread-state teardown "
                    f"thread_id={tid} "
                    "had_active_turn=true listener_generation=9",
                    tid, process,
                ),
                (
                    1_789_900_070, 8, "TRACE",
                    "codex_core::session::world_state",
                    "building step world state selected_capability_root_count=2 cwd=/SECRET/PRIVATE/PATH",
                    tid, process,
                ),
                (
                    1_789_900_080, 9, "INFO",
                    "codex_core::session::handlers",
                    "prompt=SECRET_PROMPT user_message=SECRET_CONVERSATION tool_name=SECRET_TOOL",
                    tid, process,
                ),
                (
                    1_789_900_090, 10, "TRACE",
                    "codex_core::tools::spec_plan",
                    "append_dynamic_tool_runtimes dynamic_tool_count=99 tool_spec_count=199",
                    other_tid, process,
                ),
            ]
            connection.executemany("INSERT INTO logs VALUES (?,?,?,?,?,?,?)", rows)
            connection.commit()
            connection.close()

            summary = module.read_native_codex_structural_log(
                home,
                2,
                datetime.fromtimestamp(1_789_899_000, timezone.utc),
            )
            self.assertTrue(summary["available"])
            self.assertEqual(summary["parsedRowCount"], 11)
            self.assertEqual(summary["warningCount"], 1)
            self.assertEqual(summary["toolCatalog"]["sampleCount"], 2)
            self.assertEqual(summary["toolCatalog"]["lastToolCount"], 267)
            self.assertEqual(summary["toolCatalog"]["toolCountChangeCount"], 1)
            self.assertEqual(summary["toolCatalog"]["unavailableServerSampleCount"], 1)
            self.assertEqual(summary["toolPlanning"]["dynamicToolCountSampleCount"], 3)
            self.assertEqual(summary["toolPlanning"]["minDynamicToolCount"], 7)
            self.assertEqual(summary["toolPlanning"]["maxDynamicToolCount"], 99)
            self.assertEqual(summary["toolPlanning"]["lastDynamicToolCount"], 99)
            self.assertEqual(summary["toolPlanning"]["dynamicToolCountChangeCount"], 2)
            self.assertEqual(summary["toolPlanning"]["toolSpecCountSampleCount"], 3)
            self.assertEqual(summary["toolPlanning"]["minToolSpecCount"], 19)
            self.assertEqual(summary["toolPlanning"]["maxToolSpecCount"], 199)
            self.assertEqual(summary["toolPlanning"]["lastToolSpecCount"], 199)
            self.assertEqual(summary["toolPlanning"]["toolSpecCountChangeCount"], 2)
            self.assertEqual(
                summary["mcpService"]["eventCounts"],
                [
                    {"server": "node_repl", "event": "initialized", "count": 1},
                    {"server": "other", "event": "task_cancelled", "count": 1},
                ],
            )
            self.assertEqual(summary["mcpService"]["dynamicToolCount"]["min"], 5)
            self.assertEqual(summary["mcpService"]["dynamicToolCount"]["max"], 7)
            self.assertEqual(summary["threadLifecycle"]["resumeWithActiveTurnCount"], 1)
            self.assertEqual(summary["threadState"]["teardownWithActiveTurnCount"], 1)
            self.assertEqual(summary["worldState"]["lastSelectedCapabilityRootCount"], 2)
            rendered = str(summary)
            for secret in (
                "SECRET_REQUEST", "SECRET_CONNECTION", "private_customer_connector",
                "SECRET_PEER", "SECRET", "SECRET_PROMPT", "SECRET_CONVERSATION", "SECRET_TOOL",
                "SECRET_TOOL_NAMES", "SECRET_ARGS", "SECRET_PRIVATE_NAME",
            ):
                self.assertNotIn(secret, rendered)
            self.assertNotIn("01a0b9e2-a2e8-7d20-9cb4-37dbcbbe2eb6", rendered)

            correlated = module.read_native_codex_thread_correlation(
                home,
                2,
                datetime.fromtimestamp(1_789_899_000, timezone.utc),
                tid,
            )
            self.assertTrue(correlated["available"])
            self.assertEqual(correlated["requestedThread"], module.opaque_id(tid))
            self.assertEqual(correlated["matchedThreadRowCount"], 6)
            self.assertEqual(correlated["matchedProcessCount"], 1)
            self.assertEqual(correlated["thread"]["toolPlanning"]["lastDynamicToolCount"], 7)
            self.assertEqual(correlated["thread"]["toolPlanning"]["maxDynamicToolCount"], 11)
            self.assertEqual(correlated["thread"]["toolPlanning"]["lastToolSpecCount"], 19)
            self.assertEqual(correlated["thread"]["toolPlanning"]["maxToolSpecCount"], 23)
            self.assertEqual(correlated["thread"]["worldState"]["lastSelectedCapabilityRootCount"], 2)
            self.assertEqual(correlated["processRuntime"]["toolCatalog"]["lastToolCount"], 267)
            self.assertEqual(correlated["processRuntime"]["mcpService"]["dynamicToolCount"]["max"], 7)
            correlated_text = str(correlated)
            self.assertNotIn(tid, correlated_text)
            self.assertNotIn(other_tid, correlated_text)

    def test_native_codex_structural_log_missing_database_is_nonfatal(self):
        with tempfile.TemporaryDirectory(prefix="profile-diagnostic-no-db-", dir=ROOT) as root:
            summary = module.read_native_codex_structural_log(
                Path(root),
                2,
                datetime.fromtimestamp(1_789_899_000, timezone.utc),
            )
            self.assertEqual(
                summary,
                {"available": False, "reason": "native_log_database_missing"},
            )


if __name__ == "__main__":
    unittest.main()
