from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from .domain import DEFAULT_MANAGED_PROFILE_INDEX
from .manager import (
    Profile,
    _launch_target_attached,
    launch_target,
    quit_target,
    public_targets,
    serve_target,
    supervise_target,
    target_session,
)
from .model_list_overlay import ModelListOverlay
from .platforms import current_platform
from .routing import ResponsesRoute
from .version import __version__


PUBLIC_COMMANDS = (
    "install",
    "launch",
    "status",
    "refresh",
    "uninstall",
    "targets",
    "launch-target",
    "quit-target",
    "target-session",
    "diagnostics-on",
    "diagnostics-off",
    "diagnostics-status",
)
INTERNAL_COMMANDS = (
    "_serve-target",
    "_launch-target-attached",
    "_supervise-target",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage Plura Desktop profiles for ChatGPT Desktop."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Plura Desktop {__version__}",
    )
    parser.add_argument(
        "command",
        choices=PUBLIC_COMMANDS + INTERNAL_COMMANDS,
        metavar="COMMAND",
        help="Public commands: " + ", ".join(PUBLIC_COMMANDS),
    )
    parser.add_argument(
        "--profile",
        type=int,
        default=DEFAULT_MANAGED_PROFILE_INDEX,
        help="Managed profile index (2-99; default: 2)",
    )
    parser.add_argument("--target", help="Target ID from targets --json")
    parser.add_argument(
        "--app-server-url",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--renderer-cdp-port",
        type=int,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--listen",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--responses-base-url",
        help=(
            "Optional loopback http://.../v1 Responses provider for launch-target. "
            "Requires --responses-env-key."
        ),
    )
    parser.add_argument(
        "--responses-env-key",
        help=(
            "Environment variable containing the Responses provider bearer secret. "
            "The secret value is inherited, never placed on the command line."
        ),
    )
    parser.add_argument(
        "--model-list-overlay-url",
        help=(
            "Optional loopback http:// callback that may transform app-server model/list results. "
            "Requires --model-list-overlay-env-key. Callback failure preserves the Native result."
        ),
    )
    parser.add_argument(
        "--model-list-overlay-env-key",
        help=(
            "Environment variable containing the model-list overlay bearer secret. "
            "The secret value is inherited, never placed on the command line."
        ),
    )
    parser.add_argument(
        "--app",
        help=(
            "ChatGPT executable path. macOS has a canonical default; Windows/Linux "
            "may require this or CHATGPT_EXECUTABLE."
        ),
    )
    parser.add_argument(
        "--renderer-cdp",
        action="store_true",
        help=(
            "Expose a loopback-only Chromium renderer debugging endpoint for this "
            "canonical target session."
        ),
    )
    parser.add_argument("--yes", action="store_true", help="Apply uninstall; default is dry run")
    parser.add_argument("--json", action="store_true", help="Emit the stable JSON contract where supported")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(os, "umask"):
        os.umask(0o077)
    args = build_parser().parse_args(argv)
    try:
        responses_route = None
        if args.responses_base_url is not None or args.responses_env_key is not None:
            if not args.responses_base_url or not args.responses_env_key:
                raise ValueError("Responses routing requires both --responses-base-url and --responses-env-key")
            credential = os.environ.get(args.responses_env_key)
            if credential is None:
                raise ValueError(
                    f"Responses route credential environment variable is missing: {args.responses_env_key}"
                )
            responses_route = ResponsesRoute.create(
                args.responses_base_url,
                args.responses_env_key,
                credential,
            )
        model_list_overlay = None
        if args.model_list_overlay_url is not None or args.model_list_overlay_env_key is not None:
            if not args.model_list_overlay_url or not args.model_list_overlay_env_key:
                raise ValueError(
                    "Model-list overlay requires both --model-list-overlay-url and --model-list-overlay-env-key"
                )
            overlay_credential = os.environ.get(args.model_list_overlay_env_key)
            if overlay_credential is None:
                raise ValueError(
                    "Model-list overlay credential environment variable is missing: "
                    f"{args.model_list_overlay_env_key}"
                )
            model_list_overlay = ModelListOverlay.create(
                args.model_list_overlay_url,
                args.model_list_overlay_env_key,
                overlay_credential,
            )
        platform = current_platform(app_override=args.app)
        if args.command == "targets":
            value = public_targets(platform)
            if args.json:
                print(json.dumps(value, indent=2, sort_keys=True))
            else:
                for target in value["targets"]:
                    state = target.get("state", "external")
                    print(f"{target['id']}: {target['displayName']} ({state})")
            return 0

        if args.command == "launch-target":
            if not args.target:
                raise ValueError("launch-target requires --target")
            launch_target(
                platform,
                args.target,
                responses_route=responses_route,
                model_list_overlay=model_list_overlay,
                renderer_cdp=args.renderer_cdp,
            )
            if args.json:
                print(json.dumps(target_session(platform, args.target), indent=2, sort_keys=True))
            return 0
        if args.command == "target-session":
            if not args.target:
                raise ValueError("target-session requires --target")
            value = target_session(platform, args.target)
            if args.json:
                print(json.dumps(value, indent=2, sort_keys=True))
            else:
                print(f"{value['targetID']}: {value['state']}")
            return 0
        if args.command == "quit-target":
            if not args.target:
                raise ValueError("quit-target requires --target")
            value = quit_target(platform, args.target)
            if args.json:
                print(json.dumps(value, indent=2, sort_keys=True))
            else:
                print(f"{value['targetID']}: {value['state']}")
            return 0
        if args.command == "_serve-target":
            if not args.target or not args.listen:
                raise ValueError("_serve-target requires --target and --listen")
            serve_target(platform, args.target, args.listen, responses_route=responses_route)
            return 0
        if args.command == "_launch-target-attached":
            if not args.target or not args.app_server_url:
                raise ValueError("_launch-target-attached requires --target and --app-server-url")
            _launch_target_attached(
                platform,
                args.target,
                args.app_server_url,
                renderer_cdp_port=args.renderer_cdp_port,
            )
            return 0
        if args.command == "_supervise-target":
            if not args.target:
                raise ValueError("_supervise-target requires --target")
            return supervise_target(
                platform,
                args.target,
                responses_route=responses_route,
                model_list_overlay=model_list_overlay,
                renderer_cdp=args.renderer_cdp,
            )

        profile = Profile(index=args.profile, platform=platform)
        if args.command == "uninstall":
            profile.uninstall(args.yes)
        elif args.command == "status":
            profile.status(json_output=args.json)
        elif args.command == "diagnostics-on":
            profile.set_tool_lifecycle_diagnostics(True)
        elif args.command == "diagnostics-off":
            profile.set_tool_lifecycle_diagnostics(False)
        elif args.command == "diagnostics-status":
            profile.diagnostics_status()
        elif args.command == "launch":
            launch_target(platform, profile.identifier)
        else:
            getattr(profile, args.command)()
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        subprocess.SubprocessError,
    ) as error:
        print("Error:", error, file=sys.stderr)
        if args.command == "launch":
            try:
                profile.platform.show_launch_error(profile.layout, error)
            except Exception:
                pass
        return 1
    return 0
