"""Allowed CORS origins, read from the CORS_ORIGINS environment variable.

Terraform already passes CORS_ORIGINS to every service, but the app used to
hard-code "*" and ignore it. A comma-separated list narrows the origins;
unset, empty or "*" allows every origin.
"""

import os
from typing import Any, Dict, List, Optional

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


def get_cors_origin_options() -> Dict[str, Any]:
    """Origin arguments for Starlette's CORSMiddleware."""
    origins = get_cors_allow_origins()
    if origins == [WILDCARD]:
        # Starlette answers "*" to a credentialed request that carries no cookie
        # yet, which browsers refuse; echoing every origin allows the same set.
        return {"allow_origins": [], "allow_origin_regex": ".*"}
    return {"allow_origins": origins}
