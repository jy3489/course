"""Server configuration, read from a JSON file. Every field has a default."""

import ipaddress
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class _Section(BaseModel):
    # Typos in a config file should fail loudly, not be silently ignored.
    model_config = ConfigDict(extra="forbid", frozen=True)


class ServerConfig(_Section):
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 8000
    # How long an idle keep-alive connection stays open. uvicorn's own default (5s) is
    # shorter than the pauses between a person's clicks, and proxies that reuse
    # connections without noticing they were closed (and don't retry) then fail the next
    # request with errors like "Connection closed by remote host".
    keep_alive_timeout_seconds: Annotated[int, Field(ge=1)] = 3600
    # Origins whose web pages may call the API from a browser ("*" is any origin).
    # Use [] when a reverse proxy (e.g. Caddy) already adds the CORS headers: if both
    # do, every response carries two Access-Control-Allow-Origin headers and browsers
    # reject it.
    cors_allow_origins: tuple[str, ...] = ("*",)
    # The site's public address, e.g. "https://buildplay.fun", for invite links and
    # QR codes. Empty uses the address each request came to, which can be wrong
    # behind a load balancer or reverse proxy (http instead of https, internal host).
    public_base_url: str = ""


class DataFileConfig(_Section):
    # "~" is expanded; relative paths are relative to the config file's directory.
    path: Path = Path("~/.local/share/game-platform/data.json")
    # After a change, the file is rewritten at most once per this many seconds.
    save_interval_seconds: Annotated[float, Field(ge=0)] = 1.0
    # Timestamped copies of the data file, in "<path>.backups/", so a corrupted or
    # wiped file can be restored. At most one copy per interval; 0 turns backups off.
    backup_interval_seconds: Annotated[float, Field(ge=0)] = 3600.0
    # How many of the newest copies to keep.
    backup_keep: Annotated[int, Field(ge=1)] = 48


class ModelApiConfig(_Section):
    # Meta Model API key (get one at https://dev.meta.ai). Empty disables
    # model features. Prefer the MUSE_SPARK_API_KEY environment variable
    # over storing the key in this file.
    api_key: str = ""
    model: str = "muse-spark-1.3"
    base_url: str = "https://api.ai.meta.com/v1"


class GoogleAuthConfig(_Section):
    # Google OAuth client ID for the website (Google Cloud Console > APIs & Services >
    # Credentials > Create Credentials > OAuth client ID, type "Web application", with
    # the site's origin as an authorized JavaScript origin). Empty disables Google login.
    client_id: str = ""


class SmsAuthConfig(_Section):
    # How login codes are texted. "log" writes them to the server log (development
    # only); "twilio" sends real texts (https://www.twilio.com).
    provider: Literal["log", "twilio", "verify"] = "log"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_verify_service_sid: str = ""
    # The Twilio number texts come from, in E.164 format, e.g. +15551234567.
    twilio_from_number: str = ""
    twilio_voice_enabled: bool = False


class EmailAuthConfig(_Section):
    # How login codes are emailed. "log" writes them to the server log (development
    # only); "smtp" sends real email through any SMTP server.
    provider: Literal["log", "smtp"] = "log"
    smtp_host: str = ""
    smtp_port: Annotated[int, Field(ge=1, le=65535)] = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = ""
    smtp_use_tls: bool = True


class AuthConfig(_Section):
    google: GoogleAuthConfig = GoogleAuthConfig()
    sms: SmsAuthConfig = SmsAuthConfig()
    email: EmailAuthConfig = EmailAuthConfig()


class PushConfig(_Section):
    # How push notifications are delivered. "log" writes them to the server log
    # (development only); "webpush" delivers real Web Push messages (RFC 8030),
    # signed with the VAPID key below.
    provider: Literal["log", "webpush"] = "log"
    # VAPID private key (base64url-encoded PKCS#8 DER), generated with
    # --generate-vapid-keys. Needed when provider is "webpush". Prefer the
    # GAME_PLATFORM_VAPID_PRIVATE_KEY environment variable over storing the key here.
    vapid_private_key: str = ""
    # Operator contact that push services require with VAPID: a "mailto:" or
    # "https:" URL. Needed when provider is "webpush".
    vapid_subject: str = ""
    # A push service drops a message it couldn't deliver after this many seconds.
    ttl_seconds: Annotated[int, Field(ge=0)] = 86400
    # Each user keeps at most this many subscriptions; extra ones replace the oldest.
    max_subscriptions_per_user: Annotated[int, Field(ge=1)] = 10


class FirestoreConfig(_Section):
    # query mode requires a migrated, explicitly named namespace.
    mode: Literal["snapshot", "query"] = "snapshot"
    namespace: str = ""
    # Save the data in Firestore instead of the data file (see "Firestore" in the
    # README). data_file is then unused.
    enabled: bool = False
    # The Google Cloud project. Empty uses the project of the server's credentials
    # (on a Google Cloud VM, the VM's project).
    project_id: str = ""
    database: str = "(default)"
    # A separate acknowledgement is required before using the query schema in
    # the existing production database. It never migrates data at startup.
    allow_default_database_query: bool = False
    # Prepended to every collection name, to keep several servers (e.g. staging) apart
    # in one database.
    collection_prefix: str = ""
    # After a change, Firestore is written at most once per this many seconds.
    save_interval_seconds: Annotated[float, Field(ge=0)] = 1.0

    @model_validator(mode="after")
    def _validate_query_mode(self):
        if self.enabled and self.mode == "query":
            if (
                not self.project_id
                or not self.namespace
                or not self.namespace.replace("_", "").replace("-", "").isalnum()
            ):
                raise ValueError(
                    "query mode requires an explicit project_id and alphanumeric namespace"
                )
            if self.collection_prefix:
                raise ValueError("query mode uses namespace, not collection_prefix")
        return self


class Config(_Section):
    server: ServerConfig = ServerConfig()
    data_file: DataFileConfig = DataFileConfig()
    firestore: FirestoreConfig = FirestoreConfig()
    # Serve the /console and /browse pages and /debug/all-data, which expose all data.
    debug_tools: bool = True
    # Protects /browse and /debug/all-data (HTTP basic auth, any username). Empty means
    # no password, which is only allowed when [server.host] is a numeric IP address.
    master_password: str = ""
    log_level: LogLevel = "INFO"
    model_api: ModelApiConfig = ModelApiConfig()
    auth: AuthConfig = AuthConfig()
    push: PushConfig = PushConfig()
    # Seconds the server waits before a computer opponent moves in a v2 match
    # (server-side game API), so humans can watch the game unfold. 0 moves
    # immediately.
    computer_move_delay_seconds: float = 0.6
    # How many times the game-building agent may regenerate a game's server
    # code after it fails its random-walk tests, feeding each failure report
    # back to the model (server-side game API). 0 disables the fix loop.
    ai_max_fix_attempts: Annotated[int, Field(ge=0)] = 3

    @model_validator(mode="after")
    def _require_master_password_for_hostnames(self) -> "Config":
        if (
            self.debug_tools
            and not self.master_password
            and not _is_ip_address(self.server.host)
        ):
            raise ValueError(
                f"master_password must be set when server.host ({self.server.host!r}) "
                "is a hostname rather than a numeric IP address"
            )
        return self


def _is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def load_config(path: Path) -> Config:
    """Raises [pydantic.ValidationError] if the file is invalid."""
    config = Config.model_validate_json(path.read_bytes())
    data_file = config.data_file.path.expanduser()
    if not data_file.is_absolute():
        data_file = path.parent / data_file
    return config.model_copy(
        update={
            "data_file": config.data_file.model_copy(
                update={"path": data_file.resolve()}
            )
        }
    )
