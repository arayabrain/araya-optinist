from unittest.mock import patch

import pymysql

from studio.app.common.db import config


def test_ssl_creator_sets_found_rows():
    with patch.object(config.DATABASE_CONFIG, "MYSQL_SSL_MODE", "REQUIRED"), patch(
        "pymysql.connect"
    ) as connect:
        config.get_ssl_creator()()

    client_flag = connect.call_args.kwargs.get("client_flag", 0)
    assert client_flag & pymysql.constants.CLIENT.FOUND_ROWS
