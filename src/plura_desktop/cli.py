from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from .domain import DEFAULT_MANAGED_PROFILE_INDEX
from .manager import Profile, launch_target, public_targets, quit_target, target_session
from .platforms import current_platform
from .version import __version__

PUBLIC_COMMANDS = (
    "install", "launch", "status", "refresh", "uninstall", "targets",
    "launch-target", "quit-target", "target-session",
    "diagnostics-on", "diagnostics-off", "diagnostics-status",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage isolated profiles for official ChatGPT Desktop.")
    parser.add_argument("--version", action="version", version=f"Plura Desktop {__version__}")
    parser.add_argument("command", choices=PUBLIC_COMMANDS)
    parser.add_argument("--profile", type=int, default=DEFAULT_MANAGED_PROFILE_INDEX,
                        help="Managed profile index (2-99; default: 2)")
    parser.add_argument("--target", help="Target ID from targets --json")
    parser.add_argument("--expected-desktop-pid", type=int,
                        help="For quit-target, require the exact Desktop PID observed before confirmation")
    parser.add_argument("--activate", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--app", help="Official ChatGPT executable path")
    parser.add_argument("--yes", action="store_true", help="Apply uninstall; default is dry run")
    parser.add_argument("--json", action="store_true", help="Emit the public version 2 contract")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(os, "umask"):
        os.umask(0o077)
    args = build_parser().parse_args(argv)
    try:
        platform = current_platform(app_override=args.app)
        if args.activate and (platform.platform_id != "macos" or args.command != "launch-target"):
            raise ValueError("--activate requires macOS launch-target")
        if args.command == "targets":
            value = public_targets(platform)
            if args.json:
                print(json.dumps(value, indent=2, sort_keys=True))
            else:
                for target in value["targets"]:
                    print(f"{target['id']}: {target['displayName']} ({target['state']})")
            return 0
        if args.command in {"launch-target", "target-session", "quit-target"}:
            if not args.target:
                raise ValueError(f"{args.command} requires --target")
            if args.command == "quit-target":
                value = quit_target(platform, args.target, expected_desktop_pid=args.expected_desktop_pid)
            elif args.command == "launch-target":
                value = launch_target(platform, args.target, activate=args.activate)
            else:
                value = target_session(platform, args.target)
            if args.json:
                print(json.dumps(value, indent=2, sort_keys=True))
            else:
                print(f"{value['targetID']}: {value['state']}")
            return 0
        profile = Profile(index=args.profile, platform=platform)
        if args.command == "uninstall":
            profile.uninstall(args.yes)
        elif args.command == "status":
            profile.status(json_output=args.json)
        elif args.command in {"diagnostics-on", "diagnostics-off"}:
            profile.set_tool_lifecycle_diagnostics(args.command == "diagnostics-on")
        elif args.command == "diagnostics-status":
            profile.diagnostics_status()
        elif args.command == "launch":
            launch_target(platform, profile.identifier)
        else:
            getattr(profile, args.command)()
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        print("Error:", error, file=sys.stderr)
        if args.command == "launch":
            try:
                profile.platform.show_launch_error(profile.layout, error)
            except Exception:
                pass
        return 1
    return 0
