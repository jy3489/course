"""Runs the game platform HTTP server."""

import argparse
import logging
import os
from pathlib import Path

import uvicorn

from game_platform import observability
from game_platform.api import create_app
from game_platform.config import Config, load_config
from game_platform.json_file_store import JsonFileStore
from game_platform.saving_store import SavingStore
from game_platform.push import PushNotifier, generate_vapid_keys, make_push_notifier
from game_platform.senders import make_email_sender, make_sms_sender, make_voice_sender, make_verify_client
from game_platform.service import GamePlatform

logger = logging.getLogger(__name__)


def warn_about_insecure_setup(config: Config, platform: GamePlatform) -> None:
    """Logs problems that are fine in development and dangerous on the internet."""
    if config.debug_tools and not config.master_password:
        logger.warning(
            "debug_tools is on with no master_password: anyone who can reach this "
            "server can read every user's password via /browse and /debug/all-data"
        )
    weak = [u.id for u in platform.all_data().users if u.password == u.id]
    if weak:
        logger.warning(
            "%d user(s) have their own id as password (example data?): %s. Anyone "
            "can act as them; remove them with --purge-example-data",
            len(weak),
            ", ".join(weak[:10]),
        )


def purge_example_data(platform: GamePlatform) -> None:
    """Deletes the users that the example data file creates (password == id), with
    what they own. Real users' passwords are random, so they never match."""
    for user in platform.all_data().users:
        if user.password == user.id:
            platform.admin_delete_user(user.id)
            print(f"deleted {user.id}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        help="JSON config file (see config.example.json); omitted fields, or all of "
        "them without this flag, take their defaults",
    )
    parser.add_argument(
        "--purge-example-data",
        action="store_true",
        help="delete every user whose password equals their id (the example data's "
        "users) with their games and matches, save, and exit; stop the server first",
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="print the effective config as JSON and exit",
    )
    parser.add_argument(
        "--generate-vapid-keys",
        action="store_true",
        help="generate a VAPID key pair for web push notifications and exit: the "
        "private key goes in the config's push.vapid_private_key (or the "
        "GAME_PLATFORM_VAPID_PRIVATE_KEY environment variable); the public key is "
        "served to browsers by /push/config",
    )
    args = parser.parse_args()
    if args.generate_vapid_keys:
        private_key, public_key = generate_vapid_keys()
        print(f'push.vapid_private_key: "{private_key}"')
        print(f"public key (served to browsers by /push/config): {public_key}")
        return
    config = load_config(args.config) if args.config is not None else Config()
    if args.print_config:
        print(config.model_dump_json(indent=2))
        return
    push_config = config.push
    if env_key := os.environ.get("GAME_PLATFORM_VAPID_PRIVATE_KEY"):
        push_config = push_config.model_copy(update={"vapid_private_key": env_key})

    observability.setup_logging(
        config.log_level, config.monitoring.log_format, config.monitoring.log_file
    )
    monitor = observability.Monitor(config.monitoring)
    store: SavingStore
    if config.firestore.enabled:
        # Imported here so that servers using the data file don't load the client.
        from game_platform.firestore_store import FirestoreStore

        store = FirestoreStore.connect(
            project_id=config.firestore.project_id,
            database=config.firestore.database,
            collection_prefix=config.firestore.collection_prefix,
            min_write_interval_seconds=config.firestore.save_interval_seconds,
        )
        logger.info(
            "Using Firestore database %s (collection prefix %r)",
            config.firestore.database,
            config.firestore.collection_prefix,
        )
    else:
        data_file = config.data_file.path.expanduser()
        data_file.parent.mkdir(parents=True, exist_ok=True)
        store = JsonFileStore(
            data_file,
            min_write_interval_seconds=config.data_file.save_interval_seconds,
            backup_interval_seconds=config.data_file.backup_interval_seconds,
            backup_keep=config.data_file.backup_keep,
        )
        logger.info("Using data file %s", data_file)
    push_notifier: PushNotifier | None = None
    try:
        push_notifier = make_push_notifier(push_config, store=store)
        platform = GamePlatform(
            store=store,
            push_notifier=push_notifier,
            max_push_subscriptions_per_user=push_config.max_subscriptions_per_user,
            computer_move_delay_seconds=config.computer_move_delay_seconds,
        )
        if args.purge_example_data:
            purge_example_data(platform)
            return
        platform.resume_computer_moves()
        warn_about_insecure_setup(config, platform)
        app = create_app(
            platform,
            debug_tools=config.debug_tools,
            master_password=config.master_password,
            model_api_config=config.model_api,
            auth_config=config.auth,
            push_config=push_config,
            sms_sender=make_sms_sender(config.auth.sms),
            voice_sender=make_voice_sender(config.auth.sms),
            verify_client=make_verify_client(config.auth.sms),
            email_sender=make_email_sender(config.auth.email),
            cors_allow_origins=config.server.cors_allow_origins,
            public_base_url=config.server.public_base_url,
            ai_max_fix_attempts=config.ai_max_fix_attempts,
            monitor=monitor,
        )
        monitor.start()
        observability.log_event(
            "server_started",
            f"serving on {config.server.host}:{config.server.port}",
            host=config.server.host,
            port=config.server.port,
            store="firestore" if config.firestore.enabled else "data_file",
            log_format=config.monitoring.log_format,
        )
        uvicorn.run(
            app,
            host=config.server.host,
            port=config.server.port,
            timeout_keep_alive=config.server.keep_alive_timeout_seconds,
            log_level=config.log_level.lower(),
            # Use the logging set up above, so uvicorn's lines are JSON too.
            log_config=None,
        )
    finally:
        monitor.stop()
        observability.log_event("server_stopping", "the server is shutting down")
        # Deliver queued notifications first: subscriptions found dead while draining
        # are dropped by the final write below.
        if push_notifier is not None:
            push_notifier.close()
        # Saves changes made since the last write before exiting.
        store.close()


if __name__ == "__main__":
    main()
