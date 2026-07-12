"""
URL safety checks to mitigate SSRF on crawl / bulk URL fetches.

Blocks private, loopback, link-local, and metadata IPs; requires http(s).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.utils.logger import create_logger

logger = create_logger(__name__)

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


class UnsafeURLError(ValueError):
    """Raised when a URL fails SSRF / safety checks."""


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Unwrap IPv4-mapped IPv6 (::ffff:x.x.x.x) so private/loopback checks apply.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_blocked_ip(ip.ipv4_mapped)
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return True
    if ip.is_multicast or ip.is_unspecified:
        return True
    for network in _BLOCKED_NETWORKS:
        if ip in network:
            return True
    return False


def resolve_host_ips(hostname: str) -> list[str]:
    """Resolve hostname to IP strings (IPv4/IPv6). Raises UnsafeURLError on failure."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Cannot resolve host: {hostname}") from exc
    ips: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in ips:
            ips.append(addr)
    if not ips:
        raise UnsafeURLError(f"No addresses for host: {hostname}")
    return ips


def assert_public_url(url: str, *, allow_http: bool = True) -> str:
    """
    Validate that ``url`` is a safe public http(s) target.

    Returns the normalised URL string. Raises ``UnsafeURLError`` on failure.
    """
    raw = (url or "").strip()
    if not raw:
        raise UnsafeURLError("URL is empty")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise UnsafeURLError(f"Unsupported URL scheme: {scheme or '(none)'}")
    if scheme == "http" and not allow_http:
        raise UnsafeURLError("HTTP URLs are not allowed")

    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL missing hostname")

    # Literal IP in hostname
    try:
        ip = ipaddress.ip_address(host)
        if _is_blocked_ip(ip):
            raise UnsafeURLError(f"Blocked IP address: {host}")
        return raw
    except ValueError:
        pass  # hostname is not a literal IP

    # Block obvious local names without DNS
    lowered = host.lower()
    if lowered in ("localhost", "localhost.localdomain") or lowered.endswith(".local"):
        raise UnsafeURLError(f"Blocked hostname: {host}")

    for addr in resolve_host_ips(host):
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            logger.warning("SSRF blocked host=%s resolved=%s", host, addr)
            raise UnsafeURLError(
                f"Host resolves to a private/blocked address: {host} → {addr}"
            )

    return raw


def is_safe_public_url(url: str, *, allow_http: bool = True) -> bool:
    try:
        assert_public_url(url, allow_http=allow_http)
        return True
    except UnsafeURLError:
        return False
