"""QuickBite AI + Loyalty — Application Configuration.

Pydantic BaseSettings for all environment variables.
Missing required vars = startup failure with clear ValidationError.
See Doc 2 §4 for full variable documentation.
"""

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
    AI_PROVIDER: str = "auto"

    # --- Twilio (SMS + WhatsApp) ---
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""
    TWILIO_WHATSAPP_FROM: str = ""

    # --- SendGrid (Email) ---
    SENDGRID_API_KEY: str = ""

    # --- Stripe (Billing) ---
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

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

    @model_validator(mode="after")
    def validate_separate_jwt_keys(self) -> "Settings":
        """SECRET_KEY and CUSTOMER_SECRET_KEY must be different values."""
        if self.SECRET_KEY == self.CUSTOMER_SECRET_KEY:
            msg = "SECRET_KEY and CUSTOMER_SECRET_KEY must be different values"
            raise ValueError(msg)
        return self


settings = Settings()  # type: ignore[call-arg]
