"""Allowed CORS origins, read from the CORS_ORIGINS environment variable.

Terraform already passes CORS_ORIGINS to every service, but the app used to
hard-code "*" and ignore it. A comma-separated list narrows the origins;
unset, empty or "*" keeps the previous behaviour.
"""

import os
from typing import List, Optional

WILDCARD = "*"


def _normalize(origin: str) -> str:
    # Browsers send Origin without quotes or a trailing slash, so a value
    # written as "https://a.example/" or '*' would otherwise never match.
    return origin.strip().strip("\"'").strip().rstrip("/")


def get_cors_allow_origins(raw: Optional[str] = None) -> List[str]:
    value = os.environ.get("CORS_ORIGINS", WILDCARD) if raw is None else raw
    origins = [_normalize(origin) for origin in value.split(",")]
    origins = [origin for origin in origins if origin]
    if not origins or WILDCARD in origins:
        return [WILDCARD]
    return origins
