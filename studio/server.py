"""Command-line entry that runs the API server.

Parses the server options and starts uvicorn on ``studio.__main_unit__:app``.
Imports no app code: above one worker this process only supervises, and each
worker imports the app itself, so importing it here would hold one more copy.

Usage::

    python main.py --workers 3    # cloud-startup.sh, docker-compose
    python -m studio              # run_optinist
"""

import argparse

import uvicorn

from studio.app.common.core.logger import AppLogger, LoggingConfigHelper

logger = AppLogger.get_logger()


def main(develop_mode: bool = False):
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Override log level (takes precedence over LOG_LEVEL env var)",
    )
    timeout_keep_alive = 60
    args = parser.parse_args()

    logging_config = AppLogger.get_logging_config()

    if args.log_level:
        logging_config = LoggingConfigHelper._apply_log_level_override(
            logging_config, args.log_level
        )

    effective_level = logging_config.get("root", {}).get("level", "INFO")
    logger.info(
        f"Starting Optinist server on {args.host}:{args.port} "
        f"(log_level={effective_level})"
    )

    if develop_mode:
        if args.workers > 1:
            reload = False
            reload_options = {}
        else:
            reload = args.reload
            reload_options = {"reload_dirs": ["studio"]} if args.reload else {}

        uvicorn.run(
            "studio.__main_unit__:app",
            host=args.host,
            port=args.port,
            log_config=logging_config,
            workers=args.workers,
            timeout_keep_alive=timeout_keep_alive,
            reload=reload,
            **reload_options,
        )
    else:
        uvicorn.run(
            "studio.__main_unit__:app",
            host=args.host,
            port=args.port,
            log_config=logging_config,
            workers=args.workers,
            timeout_keep_alive=timeout_keep_alive,
            reload=False,
        )
