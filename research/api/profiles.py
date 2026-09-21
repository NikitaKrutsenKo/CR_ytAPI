"""Profile and credential management for YouTube Data API keys."""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import dotenv_values


class ApiProfiles:
    """Manages API profiles and secures API keys.

    Reads profile definitions from .env or environment variables matching
    YOUTUBE_API_KEY_<PROFILE_NAME>. Never stores keys inside experiment metadata.
    """

    def __init__(self, env_path: str | Path = ".env", environment: dict | None = None) -> None:
        values = {**dotenv_values(env_path), **(os.environ if environment is None else environment)}
        self._keys = {
            k.removeprefix("YOUTUBE_API_KEY_"): v.strip()
            for k, v in values.items()
            if k.startswith("YOUTUBE_API_KEY_") and v and v.strip()
        }
        if values.get("YOUTUBE_API_KEY"):
            self._keys.setdefault("DEFAULT", values["YOUTUBE_API_KEY"].strip())
        self.default = values.get("CREATORRADAR_DEFAULT_API_PROFILE", "DEFAULT")

    @property
    def names(self) -> list[str]:
        """List of valid profile names configured."""
        return sorted(k for k in self._keys if re.fullmatch(r"[A-Z0-9_]+", k))

    def resolve(self, name: str) -> str:
        """Resolve the API key for a specified profile name.

        Args:
            name: Configured profile name (e.g. 'DEFAULT').

        Returns:
            The raw API key string.

        Raises:
            ValueError: If the profile is missing or has an empty key.
        """
        if name not in self.names:
            raise ValueError("API profile is missing or invalid; configure .env")
        return self._keys[name]
