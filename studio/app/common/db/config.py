from typing import Any, Dict, Optional

from pydantic import BaseSettings, Field, validator
from sqlalchemy.engine import URL

from studio.app.dir_path import DIRPATH

DEFAULT_CHARSET = "utf8mb4"


def build_mysql_url(
    user: str,
    password: str,
    host: str,
    database: str,
    port: Optional[int] = None,
    ssl_mode: str = "",
    charset: str = DEFAULT_CHARSET,
) -> str:
    query: Dict[str, str] = {}
    if charset:
        query["charset"] = charset
    if ssl_mode:
        query["ssl_mode"] = ssl_mode
    url = URL.create(
        "mysql+pymysql",
        username=user,
        password=password,
        host=host,
        database=database,
        port=port,
        query=query,
    )
    return str(url)


class DatabaseConfig(BaseSettings):
    """configuration for db"""

    MYSQL_ROOT_PASSWORD: str = Field(default=True, env="MYSQL_ROOT_PASSWORD")
    MYSQL_SERVER: str = Field(default="db", env="MYSQL_SERVER")
    MYSQL_USER: str = Field(default=None, env="MYSQL_USER")
    MYSQL_PASSWORD: str = Field(default=None, env="MYSQL_PASSWORD")
    MYSQL_DATABASE: str = Field(default=None, env="MYSQL_DATABASE")
    MYSQL_SSL_MODE: str = Field(default="", env="MYSQL_SSL_MODE")
    DATABASE_URL: str = Field(default=None)

    POOL_SIZE: int = Field(default=5)

    @validator("DATABASE_URL", pre=True)
    def assemble_db_connection(cls, v: Optional[str], values: Dict[str, Any]) -> Any:
        if isinstance(v, str):
            return v
        return build_mysql_url(
            user=values.get("MYSQL_USER"),
            password=values.get("MYSQL_PASSWORD"),
            host=values.get("MYSQL_SERVER"),
            database=values.get("MYSQL_DATABASE"),
            ssl_mode=values.get("MYSQL_SSL_MODE", ""),
        )

    class Config:
        env_file = f"{DIRPATH.CONFIG_DIR}/.env"
        env_file_encoding = "utf-8"
        case_sensitive = True


DATABASE_CONFIG = DatabaseConfig()
