"""QuickBite AI + Loyalty — Application Configuration.

Pydantic BaseSettings for all environment variables.
Missing required vars = startup failure with clear ValidationError.
See Doc 2 §4 for full variable documentation.
"""

import json
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All secrets are required — the app will refuse to start without them.
    Non-secret settings have sensible defaults per Doc 2.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Core Application ---
    SECRET_KEY: str
    CUSTOMER_SECRET_KEY: str
    ENVIRONMENT: str = "local"
    DEBUG: bool = False
    ALLOWED_HOSTS: str = "localhost,127.0.0.1"

    # --- Database & Cache ---
    DATABASE_URL: str
    # Alembic-only DSN. The application connects as an unprivileged role so
    # row-level security actually applies to it (see migration 0006), which
    # means it deliberately cannot run DDL. Migrations need the owner instead.
    # Empty falls back to DATABASE_URL, so single-role setups are unaffected.
    MIGRATION_DATABASE_URL: str = ""
    # True when DATABASE_URL points at a transaction pooler (Supabase port
    # 6543, or any PgBouncer in transaction mode). Disables asyncpg's prepared
    # statement cache, which the pooler invalidates between transactions.
    # Migrations must always use the direct connection, never the pooler.
    DB_USE_TRANSACTION_POOLER: bool = False
    REDIS_URL: str = "redis://localhost:6379/0"
    POSTGIS_ENABLED: bool = True

    # --- Auth & Security ---
    JWT_ACCESS_TTL_MINUTES: int = 15
    JWT_REFRESH_TTL_DAYS: int = 30
    BCRYPT_ROUNDS: int = 12
    TOTP_ISSUER_NAME: str = "QuickBite AI"
    ENCRYPTION_KEY_V1: str

    # --- Google OAuth ---
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    # --- Customer OTP Auth ---
    CUSTOMER_JWT_TTL_DAYS: int = 7
    CUSTOMER_OTP_TTL_SECONDS: int = 300
    CUSTOMER_OTP_MAX_ATTEMPTS: int = 3
    CUSTOMER_OTP_RATE_LIMIT_SECONDS: int = 120
    CUSTOMER_OTP_DAILY_MAX: int = 5
    CUSTOMER_OTP_SMS_TEMPLATE: str = "Your QuickBite code: {otp}"

    # --- AI Providers ---
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-1.5-pro"
    # REVIEW-01 requires an uncached draft in under 3s end to end. The per-
    # provider budget is deliberately below that: on an OpenAI timeout there
    # still has to be room to fail over to Gemini and answer inside the same 3s.
    AI_REQUEST_TIMEOUT_SECONDS: float = 1.2
    AI_MAX_OUTPUT_TOKENS: int = 220
    AI_DRAFT_CACHE_TTL_SECONDS: int = 3600  # 1h, per REVIEW-01
    AI_PROVIDER: str = "auto"

    # --- Twilio (SMS + WhatsApp) ---
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""
    TWILIO_WHATSAPP_FROM: str = ""

    # --- Email (SMTP transport) ---
    # Every field has a default on purpose: `settings = Settings()` runs at
    # import, so a required field here would fail the whole test suite at
    # collection. Missing credentials are handled at send time (best-effort
    # `return False`), never at startup — a degraded email channel must not
    # stop the app from booting.
    EMAIL_ENABLED: bool = True
    EMAIL_PROVIDER: str = "smtp"  # smtp | sendgrid
    EMAIL_FROM_ADDRESS: str = "no-reply@quickbite.ai"
    EMAIL_FROM_NAME: str = "QuickBite AI"
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""  # Gmail App Password — NEVER the account password
    SMTP_SECURITY: str = "starttls"  # starttls (587) | tls (465) | none
    SMTP_TIMEOUT_SECONDS: int = 10
    # How long an operator's copy edit in static.notification_templates takes to
    # reach every worker. Staleness here is cheap; a Redis hop per send is not.
    EMAIL_TEMPLATE_CACHE_TTL_SECONDS: int = 300

    # --- SendGrid (Email — legacy, used when EMAIL_PROVIDER=sendgrid) ---
    SENDGRID_API_KEY: str = ""

    # --- Razorpay (Billing) ---
    # India-first gateway: prices are already INR paise and tenants carry
    # GSTIN/PAN, so Razorpay is the native fit. RAZORPAY_WEBHOOK_SECRET is the
    # HMAC key for X-Razorpay-Signature — it is NOT RAZORPAY_KEY_SECRET, and
    # confusing the two is the usual cause of every webhook 401'ing.
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""

    # --- Supabase Auth (external identity provider) ---
    # Only SUPABASE_PROJECT_REF gates verification (see `supabase_enabled`), so
    # an unconfigured environment simply never routes a token to this verifier
    # rather than failing at startup.
    SUPABASE_PROJECT_REF: str = ""  # the <ref> in https://<ref>.supabase.co
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""  # server-only — never ships to a client
    # Supabase caches its JWKS for 10 minutes at the edge; caching longer than
    # that would delay key revocation past the window they guarantee.
    SUPABASE_JWKS_TTL_SECONDS: int = 600

    # --- Firebase Auth + Firestore ---
    # The service account is the raw JSON blob, not a file path: containers get
    # secrets as env vars, and a path implies a mounted file we would then have
    # to keep out of the image.
    FIREBASE_PROJECT_ID: str = ""
    FIREBASE_SERVICE_ACCOUNT_JSON: str = ""
    FIRESTORE_DATABASE: str = "(default)"
    FIRESTORE_PROJECTION_ENABLED: bool = True

    # --- Firebase Web SDK (client-side config for the login page's social
    # sign-in buttons) ---
    # NOT secrets — Firebase's web config is meant to ship to the browser and
    # is scoped by Firebase Auth's authorized-domains allowlist, not by
    # keeping these values hidden. Separate from FIREBASE_SERVICE_ACCOUNT_JSON
    # above, which the server uses to verify tokens and must never reach a
    # client.
    FIREBASE_WEB_API_KEY: str = ""
    FIREBASE_WEB_AUTH_DOMAIN: str = ""
    FIREBASE_WEB_APP_ID: str = ""

    # --- External identity linking ---
    # Off by default. When off, an external token with no identity_links row is
    # rejected with IDENTITY_LINK_REQUIRED instead of provisioning anything.
    EXTERNAL_AUTH_JIT_ENABLED: bool = False

    # --- Stitch.ai (Frontend Generation) ---
    STITCH_AI_API_KEY: str = ""
    STITCH_AI_DESIGN_SYSTEM: str = "quickbite_v1"

    # --- Cloudflare R2 (Object Storage) ---
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = "quickbite-assets"

    # --- Sentry (Error Tracking) ---
    SENTRY_DSN: str = ""

    # --- Computed Properties ---
    @property
    def allowed_hosts_list(self) -> list[str]:
        """Parse ALLOWED_HOSTS CSV into a list."""
        return [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]

    @property
    def alembic_database_url(self) -> str:
        """DSN for migrations — the owner, falling back to the app's own."""
        return self.MIGRATION_DATABASE_URL or self.DATABASE_URL

    @property
    def smtp_tls_kwargs(self) -> dict[str, bool]:
        """TLS flags for aiosmtplib, derived from the single SMTP_SECURITY enum.

        aiosmtplib raises ValueError when `use_tls` and `start_tls` are both
        True. Deriving both from one enum makes that state unrepresentable —
        with two independent booleans a mistyped .env would raise inside the
        transport's best-effort `except Exception`, producing a silent,
        total email outage with only a generic log line to show for it.
        """
        if self.SMTP_SECURITY == "tls":
            return {"use_tls": True, "start_tls": False}
        if self.SMTP_SECURITY == "starttls":
            return {"use_tls": False, "start_tls": True}
        return {"use_tls": False, "start_tls": False}

    @property
    def email_from_address(self) -> str:
        """Sender address, falling back to the authenticated SMTP account.

        Gmail rewrites the From header to the authenticated account anyway
        unless the alias is verified under Settings > Accounts > "Send mail
        as", so falling back to it keeps the header honest.
        """
        return self.EMAIL_FROM_ADDRESS or self.SMTP_USERNAME

    @property
    def supabase_enabled(self) -> bool:
        """True once a project ref is configured — gates the Supabase verifier.

        A disabled provider can never be selected by token dispatch, so a dev
        box with no Supabase project cannot be tricked into that branch.
        """
        return bool(self.SUPABASE_PROJECT_REF)

    @property
    def supabase_issuer(self) -> str:
        """Exact `iss` claim Supabase mints. Compared with `==`, never a prefix.

        Substring/startswith matching is defeated by a hostile issuer such as
        `https://evil.com/#https://ref.supabase.co/auth/v1`.
        """
        return f"https://{self.SUPABASE_PROJECT_REF}.supabase.co/auth/v1"

    @property
    def supabase_jwks_url(self) -> str:
        """Public JWKS discovery endpoint for asymmetric (ES256/RS256) keys."""
        return f"{self.supabase_issuer}/.well-known/jwks.json"

    @property
    def firebase_enabled(self) -> bool:
        """Both the project id and credentials are needed — verification alone
        reads the project id, but Firestore projection needs the key too, and a
        half-configured Firebase is worse than none."""
        return bool(self.FIREBASE_PROJECT_ID and self.FIREBASE_SERVICE_ACCOUNT_JSON)

    @property
    def firebase_issuer(self) -> str:
        """Exact `iss` on a Firebase ID token. `aud` is the bare project id."""
        return f"https://securetoken.google.com/{self.FIREBASE_PROJECT_ID}"

    @property
    def firebase_web_configured(self) -> bool:
        """True once the browser-side Firebase config is present.

        Gates whether the login page renders the social sign-in buttons at
        all — showing them with no client config would just fail every click.
        Independent of `firebase_enabled`: that gates the *server's* ability
        to verify a token, this gates the *browser's* ability to mint one.
        """
        return bool(self.FIREBASE_WEB_API_KEY and self.FIREBASE_WEB_AUTH_DOMAIN and self.FIREBASE_WEB_APP_ID)

    # --- Validators ---
    @field_validator("BCRYPT_ROUNDS")
    @classmethod
    def validate_bcrypt_rounds(cls, v: int) -> int:
        """BCRYPT_ROUNDS must not be below 10 in any environment."""
        if v < 10:
            msg = "BCRYPT_ROUNDS must be >= 10 (current: %d)" % v
            raise ValueError(msg)
        return v

    @field_validator("JWT_ACCESS_TTL_MINUTES")
    @classmethod
    def validate_jwt_ttl(cls, v: int) -> int:
        """JWT_ACCESS_TTL_MINUTES must not exceed 60."""
        if v > 60:
            msg = "JWT_ACCESS_TTL_MINUTES must be <= 60 (current: %d)" % v
            raise ValueError(msg)
        return v

    @field_validator("CUSTOMER_OTP_TTL_SECONDS")
    @classmethod
    def validate_otp_ttl(cls, v: int) -> int:
        """CUSTOMER_OTP_TTL_SECONDS must not exceed 600."""
        if v > 600:
            msg = "CUSTOMER_OTP_TTL_SECONDS must be <= 600 (current: %d)" % v
            raise ValueError(msg)
        return v

    @field_validator("SMTP_PASSWORD")
    @classmethod
    def strip_smtp_password(cls, v: str) -> str:
        """Strip spaces from a Gmail App Password.

        Google displays App Passwords as 'abcd efgh ijkl mnop' — the spaces are
        presentation only. Pasting them verbatim is the single most common SMTP
        misconfiguration, and it surfaces as an opaque
        '535 5.7.8 Username and Password not accepted'.
        """
        return v.replace(" ", "")

    @field_validator("EMAIL_PROVIDER")
    @classmethod
    def validate_email_provider(cls, v: str) -> str:
        allowed = {"smtp", "sendgrid"}
        if v not in allowed:
            msg = f"EMAIL_PROVIDER must be one of {sorted(allowed)} (current: {v!r})"
            raise ValueError(msg)
        return v

    @field_validator("SMTP_SECURITY")
    @classmethod
    def validate_smtp_security(cls, v: str) -> str:
        allowed = {"starttls", "tls", "none"}
        if v not in allowed:
            msg = f"SMTP_SECURITY must be one of {sorted(allowed)} (current: {v!r})"
            raise ValueError(msg)
        return v

    @field_validator("SMTP_PORT")
    @classmethod
    def validate_smtp_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            msg = "SMTP_PORT must be between 1 and 65535 (current: %d)" % v
            raise ValueError(msg)
        return v

    @field_validator("SUPABASE_PROJECT_REF")
    @classmethod
    def validate_supabase_project_ref(cls, v: str) -> str:
        """Bare project ref only — no scheme, no dots.

        `supabase_issuer` interpolates this into a URL, so pasting the full
        `https://abc.supabase.co` would build a nonsense issuer that silently
        matches nothing. Every Supabase token would then 401 with no clue why.
        """
        if v and ("/" in v or "." in v or ":" in v):
            msg = f"SUPABASE_PROJECT_REF must be the bare ref, not a URL (current: {v!r})"
            raise ValueError(msg)
        return v

    @field_validator("FIREBASE_SERVICE_ACCOUNT_JSON")
    @classmethod
    def validate_firebase_credentials(cls, v: str) -> str:
        """Accept either the inline JSON or a path to the key file, and
        normalise to inline JSON so every consumer sees one shape.

        A path is supported because that is how Google's own tooling works
        (`GOOGLE_APPLICATION_CREDENTIALS` is a filename), so it is the form
        people reach for by habit — and hand-converting a downloaded key to a
        single line is exactly where the `private_key` newlines get mangled.

        Either way it is parsed at startup rather than at the first Firebase
        call, so a bad credential fails the deploy instead of surfacing as a
        503 hours later.
        """
        if not v:
            return v

        candidate = v.strip().strip('"').strip("'")
        # A path never starts with '{'; anything else is treated as a filename
        # rather than guessed at, so a truncated blob reports a JSON error
        # instead of a confusing "file not found".
        if not candidate.startswith("{"):
            path = Path(candidate).expanduser()
            if not path.is_file():
                msg = (
                    "FIREBASE_SERVICE_ACCOUNT_JSON looks like a path but no such "
                    f"file exists: {path}"
                )
                raise ValueError(msg)
            candidate = path.read_text(encoding="utf-8")

        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            msg = f"FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON: {exc}"
            raise ValueError(msg) from exc
        missing = {"type", "project_id", "private_key", "client_email"} - parsed.keys()
        if missing:
            msg = f"FIREBASE_SERVICE_ACCOUNT_JSON is missing keys: {sorted(missing)}"
            raise ValueError(msg)
        # Returned inline so firestore_client and firebase_auth stay unaware of
        # which form the operator supplied.
        return candidate

    @model_validator(mode="after")
    def validate_separate_jwt_keys(self) -> "Settings":
        """SECRET_KEY and CUSTOMER_SECRET_KEY must be different values."""
        if self.SECRET_KEY == self.CUSTOMER_SECRET_KEY:
            msg = "SECRET_KEY and CUSTOMER_SECRET_KEY must be different values"
            raise ValueError(msg)
        return self


settings = Settings()  # type: ignore[call-arg]
