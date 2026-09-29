"""CLI entry point for the export, purge, and notifications commands."""

from __future__ import annotations

import argparse
import logging
import sys

from .auth import READ_SCOPES, WRITE_SCOPES, Authenticator, token_cache_path
from .config import load_config
from .export import Exporter
from .graph import GraphClient
from .logging_setup import setup_logging
from .notifications import NotificationCleaner
from .purge import Purger


def _build_graph(cfg, scopes: list[str]) -> GraphClient:
    auth = Authenticator(cfg.auth, token_cache_path("graph"), scopes)
    return GraphClient(auth)


def cmd_export(cfg, log: logging.Logger) -> int:
    graph = _build_graph(cfg, READ_SCOPES)
    try:
        Exporter(cfg, graph).run()
    finally:
        graph.close()
    return 0


def cmd_purge(cfg, log: logging.Logger) -> int:
    scopes = WRITE_SCOPES.copy()
    if cfg.purge.delete_onedrive_files:
        scopes.append("Files.ReadWrite.All")
    graph = _build_graph(cfg, scopes)
    try:
        Purger(cfg, graph).run()
    finally:
        graph.close()
    return 0


def cmd_notifications(cfg, log: logging.Logger) -> int:
    cleaner = NotificationCleaner(cfg)
    try:
        cleaner.run()
    finally:
        cleaner.close()
    return 0


COMMANDS = {
    "export": cmd_export,
    "purge": cmd_purge,
    "notifications": cmd_notifications,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="teams-exporter", description=__doc__)
    parser.add_argument("command", choices=COMMANDS.keys(), help="Action to run")
    parser.add_argument("-c", "--config", default="config.yaml",
                        help="Configuration file path (default: config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable DEBUG logging (overrides the configuration)")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    level = "DEBUG" if args.verbose else cfg.log_level
    log = setup_logging(level)

    try:
        return COMMANDS[args.command](cfg, log)
    except KeyboardInterrupt:
        log.warning("Interrupted.")
        return 130
    except Exception:
        log.exception("Command '%s' failed", args.command)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
