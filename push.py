"""Delivers web push notifications.

The service decides *who* is told *what* ([GamePlatform] composes localized messages);
this module decides *how*. [PushNotifier] is the interface the service depends on.
[LogPushNotifier] writes notifications to the server log (development only).
[WebPushNotifier] encrypts and posts them to the browsers' push services (RFC 8030,
payload encryption RFC 8291, VAPID signatures RFC 8292).

Delivery never happens on the request path: [enqueue] only queues, and a worker
thread does the network I/O, so a slow push service can't stall the platform, which
serializes all requests under one lock. Notifications are best-effort: a lost one is
annoying, not corrupting, so failures are logged, not raised.
"""

import base64
import json
import logging
import queue
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush

from game_platform.config import PushConfig
from game_platform.models import WebPushSubscription
from game_platform.store import Store

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PushMessage:
    """One notification to one subscription: what to show and where a click goes."""

    subscription: WebPushSubscription
    title: str
    body: str
    # Opened when the user clicks the notification (a portal match link).
    url: str
    # Browsers replace a showing notification that has the same tag.
    tag: str


class PushNotifier(Protocol):
    def enqueue(self, messages: Sequence[PushMessage]) -> None:
        """Queues notifications for delivery. Never blocks on network I/O and never
        raises: the action a notification reports has already happened."""
        ...

    def close(self) -> None:
        """Delivers what's queued and stops the worker thread, if any."""
        ...


class LogPushNotifier:
    """Writes notifications to the log instead of delivering them. For development."""

    def enqueue(self, messages: Sequence[PushMessage]) -> None:
        for message in messages:
            logger.info(
                "push to %s (%s): %s — %s",
                message.subscription.user_id,
                message.subscription.endpoint,
                message.title,
                message.body,
            )

    def close(self) -> None:
        pass

    def deliver(self, message: PushMessage) -> None:
        self.enqueue([message])


class WebPushNotifier:
    """Sends real Web Push messages from a worker thread.

    [store] drops subscriptions whose endpoint is gone (the push service answers 404
    or 410), so dead browsers don't accumulate. It must be safe for concurrent use
    (JsonFileStore is; InMemoryStore isn't).
    """

    def __init__(self, *, config: PushConfig, store: Store) -> None:
        if not config.vapid_private_key:
            raise ValueError(
                'push.provider is "webpush" but vapid_private_key is empty: set it in '
                "the config or the GAME_PLATFORM_VAPID_PRIVATE_KEY environment variable "
                "(generate a pair with --generate-vapid-keys)"
            )
        if not config.vapid_subject:
            raise ValueError(
                'push.provider is "webpush" but vapid_subject is empty: give push '
                'services an operator contact, e.g. "mailto:you@example.com"'
            )
        # pywebpush signs with PEM, so keep the key in that form.
        self._private_pem = _load_private_key(config.vapid_private_key).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        self._subject = config.vapid_subject
        self._ttl = config.ttl_seconds
        self._store = store
        self._queue: queue.Queue[PushMessage | None] = queue.Queue()
        self._worker = threading.Thread(
            target=self._work, name="web-push-sender", daemon=True
        )
        self._worker.start()

    def enqueue(self, messages: Sequence[PushMessage]) -> None:
        for message in messages:
            self._queue.put(message)

    def close(self) -> None:
        self._queue.put(None)
        self._worker.join(timeout=10)

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                self._send(item)
            except Exception:
                # The worker must survive whatever a push service does.
                logger.exception("push delivery failed unexpectedly")
            finally:
                self._queue.task_done()

    def deliver(self, message: PushMessage) -> None:
        """Synchronous outbox delivery; transient failures remain retryable."""
        self._send(message, retry_errors=True)

    def _send(self, message: PushMessage, *, retry_errors: bool = False) -> None:
        subscription = message.subscription
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {
                        "p256dh": subscription.p256dh,
                        "auth": subscription.auth,
                    },
                },
                data=json.dumps(
                    {
                        "title": message.title,
                        "body": message.body,
                        "url": message.url,
                        "tag": message.tag,
                    }
                ),
                vapid_private_key=self._private_pem,
                vapid_claims={"sub": self._subject},
                ttl=self._ttl,
                headers={"Urgency": "high"},
            )
        except WebPushException as error:
            response = getattr(error, "response", None)
            status = response.status_code if response is not None else None
            if status in (404, 410):
                logger.info(
                    "push endpoint %s is gone (%s); dropping the subscription",
                    subscription.endpoint,
                    status,
                )
                self._store.delete_push_subscription_by_endpoint(subscription.endpoint)
            else:
                if retry_errors:
                    raise
                logger.warning("push to %s failed: %s", subscription.endpoint, error)


def make_push_notifier(config: PushConfig, store: Store) -> PushNotifier:
    """The [config.provider]'s notifier. Raises ValueError on incomplete VAPID setup."""
    if config.provider == "webpush":
        return WebPushNotifier(config=config, store=store)
    return LogPushNotifier()


# VAPID keys (RFC 8292): one EC P-256 pair per deployment. The private key signs every
# push request; browsers subscribe with the public key (applicationServerKey). Both
# are base64url strings: the private key is PKCS#8 DER, the public key the
# uncompressed point.


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def generate_vapid_keys() -> tuple[str, str]:
    """A new VAPID key pair: (private, public). The private key goes in the config's
    push.vapid_private_key (or GAME_PLATFORM_VAPID_PRIVATE_KEY); the public key is
    served to browsers by /push/config. Rotating the pair invalidates existing
    subscriptions: browsers re-subscribe with the new key."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_der = private_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_point = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64url_encode(private_der), _b64url_encode(public_point)


def _load_private_key(private_key_b64url: str) -> ec.EllipticCurvePrivateKey:
    try:
        key = serialization.load_der_private_key(
            _b64url_decode(private_key_b64url), password=None
        )
    except (ValueError, TypeError) as error:
        raise ValueError(
            "vapid_private_key is not a base64url-encoded PKCS#8 key; "
            "generate one with --generate-vapid-keys"
        ) from error
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise ValueError(
            "vapid_private_key must be a P-256 EC key; generate one with "
            "--generate-vapid-keys"
        )
    return key


def vapid_public_key(private_key_b64url: str) -> str:
    """The base64url public key browsers subscribe with, for a configured private key."""
    public_point = (
        _load_private_key(private_key_b64url)
        .public_key()
        .public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    return _b64url_encode(public_point)
