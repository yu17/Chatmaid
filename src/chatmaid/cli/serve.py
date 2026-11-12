"""The serve command."""

import argparse
import dataclasses
import sys
from pathlib import Path

from chatmaid.config import ConfigError
from chatmaid.config import load as load_config


def _serve_command(args: argparse.Namespace) -> int:
    from chatmaid.web.serve import run
    from chatmaid.web.services import Services

    if args.port is not None and not 1 <= args.port <= 65535:
        print(f"--port must be from 1 to 65535, got {args.port}", file=sys.stderr)
        return 2
    try:
        cfg = load_config(Path(args.config))
        if args.port is not None:
            cfg = dataclasses.replace(cfg, web=dataclasses.replace(cfg.web, port=args.port))
        services = Services.from_config(cfg)
        cfg.data.dir.mkdir(parents=True, exist_ok=True)
        run(services)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    except OSError as e:
        print(f"cannot listen: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


def register_serve(subparsers) -> None:
    p = subparsers.add_parser("serve", help="serve the web UI (Inbox, drafts, search) over HTTP")
    p.add_argument("--port", type=int, help="listen port (default: web.port)")
    p.set_defaults(handler=_serve_command)
