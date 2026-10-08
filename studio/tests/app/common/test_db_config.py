from unittest.mock import patch

import pymysql
import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import make_url

from studio.app.common.db import config


def test_ssl_creator_sets_found_rows_and_tls():
    with patch.object(config.DATABASE_CONFIG, "MYSQL_SSL_MODE", "REQUIRED"), patch(
        "pymysql.connect"
    ) as connect:
        config.get_ssl_creator()()

    kwargs = connect.call_args.kwargs
    assert kwargs.get("client_flag", 0) & pymysql.constants.CLIENT.FOUND_ROWS
    assert kwargs["ssl"] == config.SSL_CONNECT_ARGS


def test_ssl_creator_passes_everything_the_dialect_would():
    """creator= bypasses create_connect_args, so the two must not drift."""
    url = make_url(config.DATABASE_CONFIG.DATABASE_URL)
    _, expected = mysql.pymysql.dialect(dbapi=pymysql).create_connect_args(url)

    with patch.object(config.DATABASE_CONFIG, "MYSQL_SSL_MODE", "REQUIRED"), patch(
        "pymysql.connect"
    ) as connect:
        config.get_ssl_creator()()

    assert expected.items() <= connect.call_args.kwargs.items()


@pytest.mark.parametrize("mode", ["", "DISABLED"])
def test_no_creator_without_ssl(mode):
    with patch.object(config.DATABASE_CONFIG, "MYSQL_SSL_MODE", mode):
        assert config.get_ssl_creator() is None
