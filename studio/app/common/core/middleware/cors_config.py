"""Allowed CORS origins, read from the CORS_ORIGINS environment variable.

Terraform already passes CORS_ORIGINS to every service, but the app used to
hard-code "*" and ignore it. A comma-separated list narrows the origins;
unset, empty or "*" keeps the previous behaviour.
"""

import os
from typing import List, Optional

WILDCARD = "*"


def get_cors_allow_origins(raw: Optional[str] = None) -> List[str]:
    value = os.environ.get("CORS_ORIGINS", WILDCARD) if raw is None else raw
    origins = [origin.strip() for origin in value.split(",") if origin.strip()]
    if not origins or WILDCARD in origins:
        return [WILDCARD]
    return origins
