"""Names for the `INSTANCE_MODE` env var that selects which ECS tier this
process is running on. Set by the task definition's `environment` block.
"""

INSTANCE_MODE_ENV = "INSTANCE_MODE"

# Public-instance tasks serve the SPA shell + /api/public/dataview + the
# users_me bootstrap subset; workflow/optinist routers are gated out.
INSTANCE_MODE_PUBLIC = "public"

# Default for free/premium/background tasks (env var unset).
INSTANCE_MODE_DEFAULT = "default"
