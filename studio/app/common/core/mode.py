import os

from pydantic import BaseSettings, Field

from studio.app.dir_path import DIRPATH


class Mode(BaseSettings):
    IS_STANDALONE: bool = Field(default=True, env="IS_STANDALONE")
    IS_TEST: bool = Field(default=False, env="IS_TEST")

    @property
    def IS_MULTIUSER(self):
        return not self.IS_STANDALONE

    @property
    def IN_SNAKEMAKE_BATCH(self):
        """Check if running in Snakemake batch mode."""
        return os.environ.get("IN_SNAKEMAKE_BATCH") == "true"

    def reset_mode(self, is_standalone: bool):
        # Check function availability
        assert self.IS_TEST, "This function is only available in test mode."

        self.IS_STANDALONE = is_standalone

    class Config:
        env_file = f"{DIRPATH.CONFIG_DIR}/.env"
        env_file_encoding = "utf-8"


MODE = Mode()
