"""
Portable .env loader for multi-laptop OneDrive setup.

Machine-specific .env is stored in %LOCALAPPDATA%/family_office_DB/.env
(outside OneDrive) so each laptop has its own independent config.

Falls back to loading from the project directory if the local file
doesn't exist (backward compatibility).
"""
import os
from pathlib import Path
from dotenv import load_dotenv


def load_local_env():
    """Load .env from the local (non-OneDrive) directory."""
    local_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "family_office_DB"
    local_env = local_dir / ".env"

    if local_env.exists():
        load_dotenv(local_env)
    else:
        # Fallback: try .env in the project directory (backward compat)
        load_dotenv()
