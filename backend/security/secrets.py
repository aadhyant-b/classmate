"""Loads API keys and secrets from environment. Fails loudly on missing keys."""
import os
from dotenv import load_dotenv

load_dotenv()

REQUIRED_SECRETS = ["ANTHROPIC_API_KEY"]


def get_secret(name: str) -> str:
    """Return the named secret. Raises RuntimeError if required and missing."""
    value = os.environ.get(name, "")
    if name in REQUIRED_SECRETS and not value:
        raise RuntimeError(
            f"Missing required secret: {name}. Check your .env file."
        )
    return value


def reddit_enabled() -> bool:
    """Return True if Reddit credentials are present and not placeholder values."""
    client_id = os.environ.get("REDDIT_CLIENT_ID", "")
    return bool(client_id) and client_id != "pending_reddit_approval"
