from pydantic import BaseSettings, Field

from studio.app.dir_path import DIRPATH

# Placeholder so a fresh checkout runs without setup. Multiuser deployments
# must override it: tokens signed with a published key can be forged
# (see studio/app/common/core/auth/secret_validation.py).
INSECURE_DEFAULT_SECRET_KEY = "123456"


class AuthConfig(BaseSettings):
    REFRESH_TOKEN_EXPIRE_MINUTES: int = Field(
        default=60 * 24 * 1, env="REFRESH_TOKEN_EXPIRE_MINUTES"
    )
    # Lifetime of the locally issued access token (ExToken). It used to carry
    # no "exp" claim at all; the default matches the refresh token lifetime.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=60 * 24 * 1, gt=0, env="ACCESS_TOKEN_EXPIRE_MINUTES"
    )
    SECRET_KEY: str = Field(default=INSECURE_DEFAULT_SECRET_KEY, env="SECRET_KEY")
    USE_FIREBASE_TOKEN: bool = Field(default=True, env="USE_FIREBASE_TOKEN")
    ALGORITHM = "HS256"

    class Config:
        env_file = f"{DIRPATH.CONFIG_DIR}/.env"
        env_file_encoding = "utf-8"


AUTH_CONFIG = AuthConfig()
