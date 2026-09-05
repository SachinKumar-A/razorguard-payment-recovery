"""Load a local .env, so the checked-in .env.example is not a lie.

The repository ships an `.env.example` telling you to put credentials in
`.env`. Nothing read it. A file that documents a convention the code does not
follow is worse than no file: you set the variable, the tool reports "no
credential", and you go looking for a bug in the wrong place.

Hand-rolled rather than a dependency on `python-dotenv`, because this is
fifteen lines and the project's install should stay small enough that a
reviewer runs it without thinking.

Real environment variables always win. If you exported it, that is what you
meant, and a stale `.env` should not quietly override the shell.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional


def parse(text: str) -> Dict[str, str]:
    """Parse KEY=value lines. Ignores comments, blanks and `export` prefixes."""
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # Strip one matching pair of quotes, which people add out of habit and
        # which would otherwise end up inside the credential itself.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def load(path: Optional[str] = None, override: bool = False) -> Dict[str, str]:
    """Read `.env` into the environment. Returns what it set.

    Silent when the file is absent - running without one is the normal case,
    not an error.
    """
    target = Path(path) if path else Path.cwd() / ".env"
    if not target.is_file():
        return {}

    try:
        values = parse(target.read_text(encoding="utf-8"))
    except OSError:
        return {}

    applied = {}
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied


def describe(applied: Dict[str, str]) -> str:
    """A line safe to print. Names the keys, never the values."""
    if not applied:
        return ""
    return f"loaded {len(applied)} setting(s) from .env: {', '.join(sorted(applied))}"
