from typing import Any, Dict, Optional

from pydantic import BaseSettings, Field, validator
from sqlalchemy.engine import URL

from studio.app.dir_path import DIRPATH

DEFAULT_CHARSET = "utf8mb4"
SSL_CONNECT_ARGS = {"check_hostname": False}


def build_mysql_url(
    user: str,
    password: str,
    host: str,
    database: str,
    port: Optional[int] = None,
    charset: str = DEFAULT_CHARSET,
) -> str:
    query: Dict[str, str] = {}
    if ":" in host:
        host, port = host.split(":")
        port = int(port)

    if charset:
        query["charset"] = charset

    url = URL.create(
        "mysql+pymysql",
        username=user,
        password=password,
        host=host,
        database=database,
        port=port,
        query=query,
    )
    return url.render_as_string(hide_password=False)


def _ssl_required() -> bool:
    mode = DATABASE_CONFIG.MYSQL_SSL_MODE
    return bool(mode) and mode != "DISABLED"


def get_ssl_creator():
    """Return a pymysql creator function for SSL connections.

    Returns None when SSL is not required. SQLAlchemy 2.0.43's
    normal connection paths (URL params, connect_args, do_connect
    event) all fail to establish SSL with PyMySQL 1.4.6 + RDS
    Proxy. Using creator= bypasses SQLAlchemy's connection
    parameter processing entirely, calling pymysql.connect()
    directly with the params proven to work.
    """
    if not _ssl_required():
        return None

    import pymysql

    cfg = DATABASE_CONFIG

    def _creator():
        if ":" in cfg.MYSQL_SERVER:
            host, port = cfg.MYSQL_SERVER.split(":")
            port = int(port)
        else:
            host, port = cfg.MYSQL_SERVER, None

        return pymysql.connect(
            host=host,
            port=port,
            user=cfg.MYSQL_USER,
            password=cfg.MYSQL_PASSWORD,
            database=cfg.MYSQL_DATABASE,
            charset=DEFAULT_CHARSET,
            ssl=SSL_CONNECT_ARGS,
        )

    return _creator


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
        )

    class Config:
        env_file = f"{DIRPATH.CONFIG_DIR}/.env"
        env_file_encoding = "utf-8"
        case_sensitive = True


DATABASE_CONFIG = DatabaseConfig()
