#!/usr/bin/env python3
"""Summarize profile/session lifecycle evidence from ChatGPT/Codex structural logs.

This is an on-demand, read-only diagnostic. It reads the official ChatGPT desktop text log and
only an explicit allowlist of structural targets from Codex's logs_2.sqlite. It never opens state
databases, Chromium profile databases, cookies, authentication files, or conversation content.
Conversation/thread/project identifiers are hashed before they leave the parser.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from urllib.parse import quote

from .diagnostic_artifacts import (
    DIAGNOSTIC_ARTIFACT_MAX_BYTES,
    DIAGNOSTIC_ARTIFACT_MAX_INCIDENTS,
    diagnostic_incidents_root,
    write_diagnostic_artifact as write_product_diagnostic_artifact,
)
from .version import __version__


OFFICIAL_EXECUTABLE = "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT"
UUID_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
PROJECT_BASE_RE = r"g-p-[0-9a-fA-F]{32}"
NATIVE_CODEX_STRUCTURAL_TARGETS = (
    "codex_mcp::connection_manager::tool_catalog",
    "codex_core::tools::spec_plan",
    "rmcp::service",
    "codex_app_server::request_processors::thread_lifecycle",
    "codex_app_server::thread_state",
    "codex_core::session::world_state",
)
KNOWN_EXECUTION_MCP_SERVERS = {
    "node_repl",
    "cua_repl",
    "codex_app",
    "codex_apps",
    "openai_artifact_template_picker",
}


def _owned_by_current_user(path: Path) -> bool:
    getuid = getattr(os, "getuid", None)
    return getuid is None or path.stat().st_uid == getuid()


def diagnostics_root(home: Path, profile: int) -> Path:
    metadata = home / "Library/Application Support/PluraDesktop"
    return diagnostic_incidents_root(metadata, profile)


def write_diagnostic_artifact(
    home: Path,
    profile: int,
    output: dict,
    *,
    incident_id: str | None = None,
    max_bytes: int = DIAGNOSTIC_ARTIFACT_MAX_BYTES,
    max_incidents: int = DIAGNOSTIC_ARTIFACT_MAX_INCIDENTS,
) -> dict:
    metadata = home / "Library/Application Support/PluraDesktop"
    return write_product_diagnostic_artifact(
        metadata,
        profile,
        output,
        incident_id=incident_id,
        max_bytes=max_bytes,
        max_incidents=max_incidents,
    )


def opaque_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def parse_bool(value: str | None) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def field(line: str, name: str) -> str | None:
    match = re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}=(?:\"([^\"]*)\"|([^\s]+))", line)
    if not match:
        return None
    return match.group(1) if match.group(1) is not None else match.group(2)


def timestamp(line: str) -> datetime | None:
    token = line.split(" ", 1)[0]
    try:
        return datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError:
        return None


def conversation_id(line: str) -> str | None:
    direct = field(line, "conversationId")
    if direct and re.fullmatch(UUID_RE, direct):
        return direct.lower()
    patterns = [
        rf'conversation_id(?:\\?")?\s*:\s*\\?"({UUID_RE})',
        rf"/conversation/({UUID_RE})(?:\s|$|[?\"'])",
        rf"ownerRoutePath=/g/[^\s]+/c/({UUID_RE})(?:\s|$|[?\"'])",
        rf"ownerRoutePath=/c/({UUID_RE})(?:\s|$|[?\"'])",
    ]
    for pattern in patterns:
        match = re.search(pattern, line)
        if match:
            return match.group(1).lower()
    return None


def project_route_identity(line: str) -> tuple[str | None, str | None]:
    """Return hashed base/full project identities from an IAB owner route, if present."""
    route = field(line, "ownerRoutePath")
    if not route or not route.startswith("/g/"):
        return None, None
    segment = route.split("/", 3)[2] if route.count("/") >= 2 else ""
    base_match = re.match(PROJECT_BASE_RE, segment)
    if not base_match:
        return None, None
    base = base_match.group(0).lower()
    return opaque_id(base), opaque_id(segment.lower())


def codex_home_for_profile(home: Path, profile: int) -> Path:
    if profile == 1:
        return home / ".codex"
    return home / f".codex-profile{profile}"


def _iso_from_epoch_seconds(value: object) -> str | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        return None


def _safe_builtin_server(body: str) -> str:
    server = field(body, "server_name")
    if server in KNOWN_EXECUTION_MCP_SERVERS:
        return server
    return "other"


def summarize_native_codex_structural_rows(rows: list[tuple[object, str, str, str]]) -> dict:
    """Summarize only allowlisted, content-free native Codex log targets.

    Callers must query only NATIVE_CODEX_STRUCTURAL_TARGETS. This parser intentionally ignores
    every unrecognized target and never returns raw log bodies, request/connection ids, paths,
    tool names, prompts, results, or arbitrary MCP server names.
    """
    tool_catalog = {
        "sampleCount": 0,
        "lastAt": None,
        "lastMcpServerCount": None,
        "lastAvailableServerCount": None,
        "lastUnavailableServerCount": None,
        "lastToolCount": None,
        "minToolCount": None,
        "maxToolCount": None,
        "toolCountChangeCount": 0,
        "unavailableServerSampleCount": 0,
    }
    previous_tool_count = None
    tool_planning = {
        "dynamicToolCountSampleCount": 0,
        "lastDynamicToolCount": None,
        "minDynamicToolCount": None,
        "maxDynamicToolCount": None,
        "dynamicToolCountChangeCount": 0,
        "toolSpecCountSampleCount": 0,
        "lastToolSpecCount": None,
        "minToolSpecCount": None,
        "maxToolSpecCount": None,
        "toolSpecCountChangeCount": 0,
    }
    previous_dynamic_tool_count = None
    previous_tool_spec_count = None
    mcp_event_counts: Counter[tuple[str, str]] = Counter()
    mcp_last: dict[str, dict] = {}
    mcp_dynamic_counts = {"sampleCount": 0, "min": None, "max": None, "last": None}
    thread_lifecycle = {
        "resumeCount": 0,
        "resumeWithActiveTurnCount": 0,
        "resumeWithoutActiveTurnCount": 0,
        "idleShutdownCount": 0,
        "lastResumeAt": None,
        "lastResumeThread": None,
        "lastActiveTurnStatus": None,
    }
    thread_state = {
        "teardownCount": 0,
        "teardownWithActiveTurnCount": 0,
        "lastTeardownAt": None,
        "lastTeardownThread": None,
    }
    world_state = {
        "sampleCount": 0,
        "lastAt": None,
        "lastSelectedCapabilityRootCount": None,
        "minSelectedCapabilityRootCount": None,
        "maxSelectedCapabilityRootCount": None,
    }
    warning_count = 0
    error_count = 0
    parsed_rows = 0

    for raw_ts, target, level, body in rows:
        if target not in NATIVE_CODEX_STRUCTURAL_TARGETS or not isinstance(body, str):
            continue
        at = _iso_from_epoch_seconds(raw_ts)
        if at is None:
            continue
        parsed_rows += 1
        normalized_level = level.lower() if isinstance(level, str) else ""
        if normalized_level in {"warn", "warning"}:
            warning_count += 1
        elif normalized_level == "error":
            error_count += 1

        if target == "codex_mcp::connection_manager::tool_catalog":
            if "built MCP tool list" not in body:
                continue
            mcp_server_count = parse_int(field(body, "mcp_server_count"))
            available = parse_int(field(body, "available_server_count"))
            unavailable = parse_int(field(body, "unavailable_server_count"))
            tool_count = parse_int(field(body, "tool_count"))
            tool_catalog["sampleCount"] += 1
            tool_catalog["lastAt"] = at
            tool_catalog["lastMcpServerCount"] = mcp_server_count
            tool_catalog["lastAvailableServerCount"] = available
            tool_catalog["lastUnavailableServerCount"] = unavailable
            tool_catalog["lastToolCount"] = tool_count
            if isinstance(unavailable, int) and unavailable > 0:
                tool_catalog["unavailableServerSampleCount"] += 1
            if isinstance(tool_count, int):
                current_min = tool_catalog["minToolCount"]
                current_max = tool_catalog["maxToolCount"]
                tool_catalog["minToolCount"] = tool_count if current_min is None else min(current_min, tool_count)
                tool_catalog["maxToolCount"] = tool_count if current_max is None else max(current_max, tool_count)
                if previous_tool_count is not None and tool_count != previous_tool_count:
                    tool_catalog["toolCountChangeCount"] += 1
                previous_tool_count = tool_count
            continue

        if target == "codex_core::tools::spec_plan":
            dynamic_tool_count = parse_int(field(body, "dynamic_tool_count"))
            tool_spec_count = parse_int(field(body, "tool_spec_count"))
            if dynamic_tool_count is not None:
                tool_planning["dynamicToolCountSampleCount"] += 1
                tool_planning["lastDynamicToolCount"] = dynamic_tool_count
                current_min = tool_planning["minDynamicToolCount"]
                current_max = tool_planning["maxDynamicToolCount"]
                tool_planning["minDynamicToolCount"] = (
                    dynamic_tool_count if current_min is None else min(current_min, dynamic_tool_count)
                )
                tool_planning["maxDynamicToolCount"] = (
                    dynamic_tool_count if current_max is None else max(current_max, dynamic_tool_count)
                )
                if (
                    previous_dynamic_tool_count is not None
                    and dynamic_tool_count != previous_dynamic_tool_count
                ):
                    tool_planning["dynamicToolCountChangeCount"] += 1
                previous_dynamic_tool_count = dynamic_tool_count
            if tool_spec_count is not None:
                tool_planning["toolSpecCountSampleCount"] += 1
                tool_planning["lastToolSpecCount"] = tool_spec_count
                current_min = tool_planning["minToolSpecCount"]
                current_max = tool_planning["maxToolSpecCount"]
                tool_planning["minToolSpecCount"] = (
                    tool_spec_count if current_min is None else min(current_min, tool_spec_count)
                )
                tool_planning["maxToolSpecCount"] = (
                    tool_spec_count if current_max is None else max(current_max, tool_spec_count)
                )
                if previous_tool_spec_count is not None and tool_spec_count != previous_tool_spec_count:
                    tool_planning["toolSpecCountChangeCount"] += 1
                previous_tool_spec_count = tool_spec_count
            continue

        if target == "rmcp::service":
            server = _safe_builtin_server(body)
            if "new Service initialized as client" in body:
                event = "initialized"
            elif "new serve finished" in body:
                event = "serve_finished"
            elif "new task cancelled" in body:
                event = "task_cancelled"
            elif "mcp runtime refresh" in body:
                event = "runtime_refresh"
            else:
                event = None
            dynamic_count = parse_int(field(body, "dynamic_tool_count"))
            if dynamic_count is not None:
                mcp_dynamic_counts["sampleCount"] += 1
                mcp_dynamic_counts["last"] = dynamic_count
                mcp_dynamic_counts["min"] = (
                    dynamic_count
                    if mcp_dynamic_counts["min"] is None
                    else min(mcp_dynamic_counts["min"], dynamic_count)
                )
                mcp_dynamic_counts["max"] = (
                    dynamic_count
                    if mcp_dynamic_counts["max"] is None
                    else max(mcp_dynamic_counts["max"], dynamic_count)
                )
            if event is not None:
                mcp_event_counts[(server, event)] += 1
                mcp_last[server] = {"at": at, "event": event}
            continue

        if target == "codex_app_server::request_processors::thread_lifecycle":
            thread = field(body, "thread_id")
            thread_hash = opaque_id(thread.lower()) if thread and re.fullmatch(UUID_RE, thread) else None
            if "composing running thread resume response" in body:
                thread_lifecycle["resumeCount"] += 1
                active = parse_bool(field(body, "active_turn_present"))
                if active is True:
                    thread_lifecycle["resumeWithActiveTurnCount"] += 1
                elif active is False:
                    thread_lifecycle["resumeWithoutActiveTurnCount"] += 1
                thread_lifecycle["lastResumeAt"] = at
                thread_lifecycle["lastResumeThread"] = thread_hash
                active_status = field(body, "active_turn_status")
                thread_lifecycle["lastActiveTurnStatus"] = (
                    active_status
                    if active_status not in {None, "None", "null"}
                    and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", active_status)
                    else None
                )
            if "has no subscribers and is idle shutting down" in body:
                thread_lifecycle["idleShutdownCount"] += 1
            continue

        if target == "codex_app_server::thread_state":
            if "clearing thread listener during thread-state teardown" not in body:
                continue
            thread_state["teardownCount"] += 1
            if parse_bool(field(body, "had_active_turn")) is True:
                thread_state["teardownWithActiveTurnCount"] += 1
            thread = field(body, "thread_id")
            thread_state["lastTeardownAt"] = at
            thread_state["lastTeardownThread"] = (
                opaque_id(thread.lower()) if thread and re.fullmatch(UUID_RE, thread) else None
            )
            continue

        if target == "codex_core::session::world_state":
            if "building step world state" not in body:
                continue
            count = parse_int(field(body, "selected_capability_root_count"))
            world_state["sampleCount"] += 1
            world_state["lastAt"] = at
            world_state["lastSelectedCapabilityRootCount"] = count
            if count is not None:
                world_state["minSelectedCapabilityRootCount"] = (
                    count
                    if world_state["minSelectedCapabilityRootCount"] is None
                    else min(world_state["minSelectedCapabilityRootCount"], count)
                )
                world_state["maxSelectedCapabilityRootCount"] = (
                    count
                    if world_state["maxSelectedCapabilityRootCount"] is None
                    else max(world_state["maxSelectedCapabilityRootCount"], count)
                )

    return {
        "available": True,
        "parsedRowCount": parsed_rows,
        "warningCount": warning_count,
        "errorCount": error_count,
        "toolCatalog": tool_catalog,
        "toolPlanning": tool_planning,
        "mcpService": {
            "eventCounts": [
                {"server": server, "event": event, "count": count}
                for (server, event), count in sorted(mcp_event_counts.items())
            ],
            "lastByServer": mcp_last,
            "dynamicToolCount": mcp_dynamic_counts,
        },
        "threadLifecycle": thread_lifecycle,
        "threadState": thread_state,
        "worldState": world_state,
    }


def read_native_codex_structural_log(
    home: Path,
    profile: int,
    cutoff: datetime,
) -> dict:
    codex_home = codex_home_for_profile(home, profile)
    database = codex_home / "logs_2.sqlite"
    if not database.exists():
        return {"available": False, "reason": "native_log_database_missing"}
    if database.is_symlink() or not database.is_file():
        raise RuntimeError("Native Codex log database must be a regular non-symlink file")
    if not _owned_by_current_user(database):
        raise RuntimeError("Native Codex log database is not owned by the current user")

    uri = f"file:{quote(str(database), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.5)
    try:
        connection.execute("PRAGMA query_only=ON")
        placeholders = ",".join("?" for _ in NATIVE_CODEX_STRUCTURAL_TARGETS)
        rows = connection.execute(
            f"""
            SELECT ts, target, level, feedback_log_body
            FROM logs
            WHERE ts >= ?
              AND target IN ({placeholders})
            ORDER BY ts ASC, ts_nanos ASC
            """,
            [cutoff.timestamp(), *NATIVE_CODEX_STRUCTURAL_TARGETS],
        ).fetchall()
    except sqlite3.DatabaseError as error:
        raise RuntimeError("Native Codex structural log query failed") from error
    finally:
        connection.close()
    result = summarize_native_codex_structural_rows(rows)
    result["database"] = database.name
    return result


def read_native_codex_thread_correlation(
    home: Path,
    profile: int,
    cutoff: datetime,
    native_thread_id: str,
) -> dict:
    """Correlate one exact Native thread with its structural Codex process evidence.

    The exact UUID is accepted only as a local query key and is never returned. The thread summary
    contains only allowlisted structural targets for that thread. Process-runtime context is limited
    to MCP catalog/service targets from the same Codex process UUID(s), so other conversations'
    thread-scoped tool planning cannot contaminate the requested thread.
    """
    if not re.fullmatch(UUID_RE, native_thread_id):
        raise RuntimeError("Native thread id must be a canonical UUID")
    native_thread_id = native_thread_id.lower()
    codex_home = codex_home_for_profile(home, profile)
    database = codex_home / "logs_2.sqlite"
    if not database.exists():
        return {
            "available": False,
            "reason": "native_log_database_missing",
            "requestedThread": opaque_id(native_thread_id),
        }
    if database.is_symlink() or not database.is_file():
        raise RuntimeError("Native Codex log database must be a regular non-symlink file")
    if not _owned_by_current_user(database):
        raise RuntimeError("Native Codex log database is not owned by the current user")

    uri = f"file:{quote(str(database), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.5)
    try:
        connection.execute("PRAGMA query_only=ON")
        placeholders = ",".join("?" for _ in NATIVE_CODEX_STRUCTURAL_TARGETS)
        thread_rows = connection.execute(
            f"""
            SELECT ts, target, level, feedback_log_body
            FROM logs
            WHERE ts >= ?
              AND lower(thread_id) = ?
              AND target IN ({placeholders})
            ORDER BY ts ASC, ts_nanos ASC
            """,
            [cutoff.timestamp(), native_thread_id, *NATIVE_CODEX_STRUCTURAL_TARGETS],
        ).fetchall()
        process_rows = connection.execute(
            f"""
            SELECT DISTINCT process_uuid
            FROM logs
            WHERE ts >= ?
              AND lower(thread_id) = ?
              AND target IN ({placeholders})
              AND process_uuid IS NOT NULL
              AND process_uuid != ''
            """,
            [cutoff.timestamp(), native_thread_id, *NATIVE_CODEX_STRUCTURAL_TARGETS],
        ).fetchall()
        process_uuids = sorted({row[0] for row in process_rows if isinstance(row[0], str) and row[0]})
        process_runtime_rows = []
        if process_uuids:
            process_placeholders = ",".join("?" for _ in process_uuids)
            process_runtime_rows = connection.execute(
                f"""
                SELECT ts, target, level, feedback_log_body
                FROM logs
                WHERE ts >= ?
                  AND process_uuid IN ({process_placeholders})
                  AND target IN (?, ?)
                ORDER BY ts ASC, ts_nanos ASC
                """,
                [
                    cutoff.timestamp(),
                    *process_uuids,
                    "codex_mcp::connection_manager::tool_catalog",
                    "rmcp::service",
                ],
            ).fetchall()
    except sqlite3.DatabaseError as error:
        raise RuntimeError("Native Codex thread correlation query failed") from error
    finally:
        connection.close()

    return {
        "available": True,
        "requestedThread": opaque_id(native_thread_id),
        "matchedThreadRowCount": len(thread_rows),
        "matchedProcessCount": len(process_uuids),
        "thread": summarize_native_codex_structural_rows(thread_rows),
        "processRuntime": summarize_native_codex_structural_rows(process_runtime_rows),
    }


def summarize_lines(lines: list[str], cutoff: datetime | None = None) -> dict:
    account_lookup = Counter()
    runtime = {"checks": 0, "last": None}
    workspace_dependencies = {
        "toolOutcomes": Counter(),
        "lastToolOutcome": None,
        "restoreFailureCount": 0,
        "updatePollFailureCount": 0,
    }
    mcp_counts: Counter[tuple[str, str]] = Counter()
    mcp_last: dict[str, dict] = {}
    app_server_transitions: Counter[tuple[str, str]] = Counter()
    remote_counts = {"refreshes": 0, "maxConnectionCount": 0, "lastConnectionCount": None}
    conversation_access: dict[str, dict] = defaultdict(
        lambda: {
            "inaccessibleCount": 0,
            "firstInaccessibleAt": None,
            "lastInaccessibleAt": None,
            "refetchStartedCount": 0,
            "refetchCompletedCount": 0,
            "lastRefetchAt": None,
            "lastRefetchMappingSize": None,
            "lastRefetchCurrentNodeApplied": None,
            "lastRefetchStatusBefore": None,
            "lastRefetchStatusAfter": None,
            "projectBase": None,
            "projectRouteVariants": set(),
        }
    )
    local_resume = {
        "count": 0,
        "lastAt": None,
        "lastLocalEnvironmentPresent": None,
        "lastAssignedStreamRole": None,
    }
    first_at = None
    last_at = None
    parsed_lines = 0

    for line in lines:
        at = timestamp(line)
        if at is None or (cutoff is not None and at < cutoff):
            continue
        parsed_lines += 1
        iso = at.isoformat().replace("+00:00", "Z")
        first_at = iso if first_at is None else first_at
        last_at = iso

        if "[chatgpt-account-lookup] completed" in line:
            result = field(line, "result") or "unknown"
            present = field(line, "authenticatedAccountPresent") or "unknown"
            account_lookup[(result, present)] += 1

        if "primary_runtime_dependencies_diagnose_finished" in line:
            runtime["checks"] += 1
            runtime["last"] = {
                "at": iso,
                "bundleVersion": field(line, "bundleVersion"),
                "installed": parse_bool(field(line, "installed")),
                "problemCount": parse_int(field(line, "problemCount")),
            }

        if "primary_runtime_dependency_tool_outcome" in line:
            outcome = field(line, "outcome")
            if outcome in {"available", "unavailable", "disabled", "failed"}:
                workspace_dependencies["toolOutcomes"][outcome] += 1
                workspace_dependencies["lastToolOutcome"] = {
                    "at": iso,
                    "outcome": outcome,
                    "bundleVersion": field(line, "bundleVersion"),
                    "durationMs": parse_int(field(line, "durationMs")),
                }
        if "primary_runtime_restore_failed" in line:
            workspace_dependencies["restoreFailureCount"] += 1
        if "primary_runtime_update_poll_failed" in line:
            workspace_dependencies["updatePollFailureCount"] += 1

        if "mcp_server_startup_status_updated" in line:
            server = field(line, "server")
            status = field(line, "status")
            if server and status and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", server):
                mcp_counts[(server, status)] += 1
                item = {"at": iso, "status": status}
                thread = field(line, "threadId")
                if thread and re.fullmatch(UUID_RE, thread):
                    item["thread"] = opaque_id(thread.lower())
                failure = field(line, "failureReason")
                if failure and failure not in {"null", "undefined"}:
                    item["failureReasonPresent"] = True
                mcp_last[server] = item

        if "app_server_connection.state_changed" in line:
            previous = field(line, "previous") or field(line, "currentState") or "unknown"
            nxt = field(line, "next") or "unknown"
            app_server_transitions[(previous, nxt)] += 1

        if (
            "[remote-connections/window-context]" in line
            and ("refresh_completed" in line or "refresh_remote_control_completed" in line)
        ):
            current = parse_int(field(line, "nextConnectionCount"))
            if current is not None:
                remote_counts["refreshes"] += 1
                remote_counts["lastConnectionCount"] = current
                remote_counts["maxConnectionCount"] = max(remote_counts["maxConnectionCount"], current)

        if "maybe_resume_success" in line:
            environments = field(line, "environmentIds") or ""
            local_resume["count"] += 1
            local_resume["lastAt"] = iso
            local_resume["lastLocalEnvironmentPresent"] = "local" in environments
            role = field(line, "assignedStreamRole")
            if role and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", role):
                local_resume["lastAssignedStreamRole"] = role

        cid = conversation_id(line)
        if cid:
            key = opaque_id(cid)
            item = conversation_access[key]
            project_base, project_variant = project_route_identity(line)
            if project_base is not None:
                if item["projectBase"] is None:
                    item["projectBase"] = project_base
                elif item["projectBase"] != project_base:
                    # Keep only the fact that more than one base was observed. Never expose either ID.
                    item["projectBase"] = "multiple"
                item["projectRouteVariants"].add(project_variant)
            if "conversation_inaccessible" in line:
                item["inaccessibleCount"] += 1
                if item["firstInaccessibleAt"] is None:
                    item["firstInaccessibleAt"] = iso
                item["lastInaccessibleAt"] = iso
            if "chatgpt_conversation_refetch_started" in line:
                item["refetchStartedCount"] += 1
            if "chatgpt_conversation_refetch_completed" in line:
                item["refetchCompletedCount"] += 1
                item["lastRefetchAt"] = iso
                item["lastRefetchMappingSize"] = parse_int(field(line, "mappingSize"))
                item["lastRefetchCurrentNodeApplied"] = parse_bool(field(line, "currentNodeApplied"))
                before = field(line, "statusBefore")
                after = field(line, "statusAfter")
                item["lastRefetchStatusBefore"] = before if before in {"idle", "streaming"} else None
                item["lastRefetchStatusAfter"] = after if after in {"idle", "streaming"} else None

    conversations = []
    access_refetch_overlaps = []
    for key, item in sorted(conversation_access.items()):
        if (
            not item["inaccessibleCount"]
            and not item["refetchStartedCount"]
            and not item["refetchCompletedCount"]
            and not item["projectRouteVariants"]
        ):
            continue
        row = {
            "conversation": key,
            **{name: value for name, value in item.items() if name != "projectRouteVariants"},
            "projectRouteVariantCount": len(item["projectRouteVariants"]),
        }
        conversations.append(row)
        if item["inaccessibleCount"] > 0 and item["refetchCompletedCount"] > 0:
            access_refetch_overlaps.append(
                {
                    "conversation": key,
                    "reason": "conversation_access_denied_while_refetch_completed",
                    "inaccessibleCount": item["inaccessibleCount"],
                    "refetchCompletedCount": item["refetchCompletedCount"],
                    "lastInaccessibleAt": item["lastInaccessibleAt"],
                    "lastRefetchAt": item["lastRefetchAt"],
                    "projectBase": item["projectBase"],
                    "projectRouteVariantCount": len(item["projectRouteVariants"]),
                }
            )

    return {
        "window": {"firstParsedAt": first_at, "lastParsedAt": last_at, "parsedLineCount": parsed_lines},
        "accountLookup": [
            {"result": result, "authenticatedAccountPresent": present, "count": count}
            for (result, present), count in sorted(account_lookup.items())
        ],
        "primaryRuntime": runtime,
        "workspaceDependencies": {
            "toolOutcomes": dict(sorted(workspace_dependencies["toolOutcomes"].items())),
            "lastToolOutcome": workspace_dependencies["lastToolOutcome"],
            "restoreFailureCount": workspace_dependencies["restoreFailureCount"],
            "updatePollFailureCount": workspace_dependencies["updatePollFailureCount"],
        },
        "mcpStartup": {
            "counts": [
                {"server": server, "status": status, "count": count}
                for (server, status), count in sorted(mcp_counts.items())
            ],
            "lastByServer": mcp_last,
        },
        "appServerTransitions": [
            {"from": previous, "to": nxt, "count": count}
            for (previous, nxt), count in sorted(app_server_transitions.items())
        ],
        "remoteConnections": remote_counts,
        "localThreadResume": local_resume,
        "conversations": conversations,
        "conversationAccessRefetchOverlaps": access_refetch_overlaps,
    }


def profile_main_pids(home: Path, profile: int) -> list[int]:
    rows = subprocess.check_output(["/bin/ps", "-axo", "pid=,args="], text=True)
    default_data = str(home / "Library/Application Support/Codex")
    selected_data = str(home / f"Library/Application Support/Codex-Profile{profile}")
    result = []
    for row in rows.splitlines():
        parts = row.strip().split(None, 1)
        if len(parts) != 2:
            continue
        command = parts[1]
        if not (command == OFFICIAL_EXECUTABLE or command.startswith(OFFICIAL_EXECUTABLE + " ")):
            continue
        markers = re.findall(r"--user-data-dir=([^\n]+?)(?=\s--[A-Za-z0-9_-]+(?:=|\s)|$)", command)
        if profile == 1:
            if not markers or markers == [default_data]:
                result.append(int(parts[0]))
        elif len(markers) == 1 and markers[0] == selected_data:
            result.append(int(parts[0]))
    return result


def current_log_for_profile(home: Path, profile: int) -> Path:
    pids = profile_main_pids(home, profile)
    if len(pids) != 1:
        raise RuntimeError(
            f"Expected exactly one running ChatGPT main process for Profile {profile}; "
            f"found {len(pids)}. Pass --log explicitly for an offline process."
        )
    root = home / "Library/Logs/com.openai.codex"
    matches = []
    pid_token = f"-{pids[0]}-"
    if root.is_dir():
        for path in root.glob("*/*/*/codex-desktop-*.log"):
            if pid_token in path.name and path.is_file() and not path.is_symlink():
                matches.append(path)
    if not matches:
        raise RuntimeError(f"No official ChatGPT desktop log found for Profile {profile}")
    return max(matches, key=lambda item: item.stat().st_mtime)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        action="version",
        version=f"Plura Desktop diagnostics {__version__}",
    )
    parser.add_argument("--profile", type=int, default=1, help="Profile index to correlate; default 1")
    parser.add_argument("--minutes", type=int, default=240, help="Only inspect this many recent minutes")
    parser.add_argument("--log", type=Path, help="Explicit official ChatGPT desktop log")
    parser.add_argument(
        "--native-thread-id",
        help="Correlate one exact Native Codex thread UUID; output contains only its hash",
    )
    parser.add_argument(
        "--write-artifact",
        action="store_true",
        help=(
            "Persist this sanitized summary under PluraDesktop diagnostics/profile/incident storage. "
            "Artifacts are size-bounded and retain at most 10 incidents per profile."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.profile < 1 or args.profile > 99:
        parser.error("--profile must be between 1 and 99")
    if args.minutes < 1 or args.minutes > 7 * 24 * 60:
        parser.error("--minutes must be between 1 and 10080")
    if args.write_artifact and args.profile < 2:
        parser.error("--write-artifact requires a managed profile between 2 and 99")
    if sys.platform != "darwin":
        print(
            "Error: plura-desktop-diagnose is an explicit macOS-only diagnostic; "
            "no cross-platform log-path fallback is defined.",
            file=sys.stderr,
        )
        return 2

    home = Path(os.environ.get("HOME") or Path.home())
    try:
        log = args.log.expanduser() if args.log else current_log_for_profile(home, args.profile)
        if log.is_symlink() or not log.is_file():
            raise RuntimeError("Diagnostic log must be a regular non-symlink file")
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=args.minutes)
        summary = summarize_lines(log.read_text(errors="replace").splitlines(), cutoff=cutoff)
        native_codex_log = read_native_codex_structural_log(home, args.profile, cutoff)
        native_thread_correlation = (
            read_native_codex_thread_correlation(
                home,
                args.profile,
                cutoff,
                args.native_thread_id,
            )
            if args.native_thread_id
            else None
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    output = {
        "profile": args.profile,
        "sourceLog": log.name,
        "lookbackMinutes": args.minutes,
        "nativeCodexStructuralLog": native_codex_log,
        "nativeCodexThreadCorrelation": native_thread_correlation,
        **summary,
    }
    if args.write_artifact:
        try:
            output["artifact"] = write_diagnostic_artifact(home, args.profile, output)
        except (OSError, RuntimeError) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0

    print(f"Profile {args.profile} official desktop session diagnostic")
    print("Source log:", log.name)
    print("Parsed window:", output["window"])
    print("Primary runtime:", output["primaryRuntime"])
    print("Workspace dependencies:", output["workspaceDependencies"])
    print("Native Codex structural log:", output["nativeCodexStructuralLog"])
    if output["nativeCodexThreadCorrelation"] is not None:
        print("Native Codex exact-thread correlation:", output["nativeCodexThreadCorrelation"])
    print("MCP last status:", output["mcpStartup"]["lastByServer"])
    print("Remote connections:", output["remoteConnections"])
    print("Local resume:", output["localThreadResume"])
    if output["conversationAccessRefetchOverlaps"]:
        print("Conversation access/refetch overlaps (correlation only, not proof of cause):")
        for item in output["conversationAccessRefetchOverlaps"]:
            print(" ", item)
    else:
        print("Conversation access/refetch overlaps: none in inspected window")
    if output.get("artifact") is not None:
        print("Saved bounded diagnostic incident:", output["artifact"]["incident"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
