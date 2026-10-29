"""python -m fake_users serve --config config.json
    the control page and health page (start the fake users from the control page)
python -m fake_users run --config config.json [--seconds 180]
    runs fake users with the config's settings for a while, then prints the health numbers"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from fake_users.config import ConfigError, load
from fake_users.generator import Generator


async def run_for(config_path: str, seconds: float) -> dict:
    generator = Generator(load(config_path))
    await generator.start()
    try:
        await asyncio.sleep(seconds)
    finally:
        await generator.stop()
    return generator.stats.snapshot()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fake_users")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run fake users for a while, then print the numbers")
    run.add_argument("--config", default="config.json")
    run.add_argument("--seconds", type=float, default=180)
    serve = sub.add_parser("serve", help="the control and health pages")
    serve.add_argument("--config", default="config.json")
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            print(json.dumps(asyncio.run(run_for(args.config, args.seconds)), indent=1))
        elif args.command == "serve":
            import uvicorn

            from fake_users.app import create_app

            config = load(args.config)
            print(f"control page: http://{config.control_host}:{config.control_port}/ (password from the config)")
            uvicorn.run(create_app(config), host=config.control_host, port=config.control_port, log_level="warning")
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
