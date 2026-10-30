"""
GET a user-supplied URL from the server, with timeouts and, where users must not
reach the server's network, from public addresses only.

Each connection then resolves its host once, refuses it unless every address is
global, and connects to the checked address. Redirects are followed through the
same connections, so every hop is checked.
"""

import ipaddress
import socket

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError
from urllib3.util.connection import allowed_gai_family, create_connection

# (connect, read) seconds. The read timeout bounds each wait for data, so a
# server that stalls mid-body also ends the read, with an error
TIMEOUT = (10, 60)
MAX_REDIRECTS = 5


class AddressNotAllowedError(NewConnectionError):
    pass


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop an IPv6 scope
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


class _PublicAddressOnly:
    def _new_conn(self):
        # Opens the socket itself: host (the Host header, TLS's server name) and
        # the name urllib3 resolves are one attribute, which must stay the name
        host = self._dns_host
        try:
            infos = socket.getaddrinfo(
                host, self.port, allowed_gai_family(), socket.SOCK_STREAM
            )
        except OSError as e:
            raise NewConnectionError(self, f"Failed to resolve {host}: {e}")
        addresses = list(dict.fromkeys(info[4][0] for info in infos))
        if not addresses or not all(map(is_public_address, addresses)):
            raise AddressNotAllowedError(
                self, f"{host} resolves to an address that is not public"
            )

        error = None
        for address in addresses:
            try:
                return create_connection(
                    (address, self.port),
                    self.timeout,
                    source_address=self.source_address,
                    socket_options=self.socket_options,
                )
            except socket.timeout:
                error = ConnectTimeoutError(
                    self, f"Connection to {host} timed out ({self.timeout} s)"
                )
            except OSError as e:
                error = NewConnectionError(
                    self, f"Failed to establish a new connection: {e}"
                )
        raise error


class _PublicHTTPConnection(_PublicAddressOnly, HTTPConnection):
    pass


class _PublicHTTPSConnection(_PublicAddressOnly, HTTPSConnection):
    pass


class _PublicHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _PublicHTTPConnection


class _PublicHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _PublicHTTPSConnection


class _PublicAddressAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            "http": _PublicHTTPConnectionPool,
            "https": _PublicHTTPSConnectionPool,
        }


def open_url(url: str, public_only: bool = True) -> requests.Response:
    """GET url as a stream; the caller reads the body and closes the response.
    public_only: connect to public addresses only, on every redirect hop."""
    with requests.Session() as session:
        session.max_redirects = MAX_REDIRECTS
        if public_only:
            # Environment proxies would make the connections instead, and
            # .netrc credentials would go to the user's host
            session.trust_env = False
            adapter = _PublicAddressAdapter()
            session.mount("http://", adapter)
            session.mount("https://", adapter)
        # identity: the bytes read are the bytes Content-Length gives
        res = session.get(
            url,
            stream=True,
            timeout=TIMEOUT,
            headers={"Accept-Encoding": "identity"},
        )
    try:
        res.raise_for_status()
    except requests.HTTPError:
        res.close()
        raise
    return res
