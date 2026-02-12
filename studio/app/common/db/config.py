from typing import Any, Dict, Optional

from pydantic import BaseSettings, Field, validator

from studio.app.dir_path import DIRPATH


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
        user = values.get("MYSQL_USER")
        password = values.get("MYSQL_PASSWORD")
        server = values.get("MYSQL_SERVER")
        database = values.get("MYSQL_DATABASE")

        url = (
            f"mysql+pymysql://{user}:{password}" f"@{server}/{database}?charset=utf8mb4"
        )
        ssl_mode = values.get("MYSQL_SSL_MODE")
        if ssl_mode:
            url += f"&ssl_mode={ssl_mode}"
        return url

    class Config:
        env_file = f"{DIRPATH.CONFIG_DIR}/.env"
        env_file_encoding = "utf-8"
        case_sensitive = True


DATABASE_CONFIG = DatabaseConfig()
