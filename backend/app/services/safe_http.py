"""SSRF-safe, size-bounded outbound HTTP client with DNS pinning."""

from __future__ import annotations

import socket
from typing import Any

import aiohttp
import httpx

from app.core.config import settings
from app.services.url_safety import UnsafeURLError, _is_blocked_ip


class ValidatedResolver(aiohttp.abc.AbstractResolver):
    """Resolve once inside the connector and return only validated addresses."""

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_UNSPEC
    ) -> list[dict[str, Any]]:
        loop = __import__("asyncio").get_running_loop()
        infos = await loop.getaddrinfo(
            host, port, family=family, type=socket.SOCK_STREAM
        )
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        import ipaddress

        for resolved_family, _, proto, _, address in infos:
            ip_text = address[0]
            if ip_text in seen:
                continue
            ip = ipaddress.ip_address(ip_text)
            if _is_blocked_ip(ip):
                raise UnsafeURLError(f"Blocked resolved address for {host}")
            seen.add(ip_text)
            records.append(
                {
                    "hostname": host,
                    "host": ip_text,
                    "port": port,
                    "family": resolved_family,
                    "proto": proto,
                    "flags": socket.AI_NUMERICHOST,
                }
            )
        if not records:
            raise UnsafeURLError(f"No safe addresses for {host}")
        return records

    async def close(self) -> None:
        return None


class SafeOutboundHttpClient:
    """Small response-compatible wrapper used by all user-controlled URL fetches."""

    def __init__(self, *, timeout: float = 30, headers: dict[str, str] | None = None):
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._headers = headers
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "SafeOutboundHttpClient":
        connector = aiohttp.TCPConnector(
            resolver=ValidatedResolver(), use_dns_cache=True, ttl_dns_cache=300
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=self._timeout,
            headers=self._headers,
            trust_env=False,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._session is not None:
            await self._session.close()

    async def get(self, url: str, *, timeout: float | None = None) -> httpx.Response:
        if self._session is None:
            raise RuntimeError(
                "SafeOutboundHttpClient must be used as a context manager"
            )
        request_options: dict[str, Any] = {"allow_redirects": False}
        if timeout is not None:
            request_options["timeout"] = aiohttp.ClientTimeout(total=timeout)
        async with self._session.get(url, **request_options) as response:
            declared = response.content_length
            limit = settings.remote_response_max_bytes
            if declared is not None and declared > limit:
                raise ValueError("Remote response exceeds configured size limit")
            body = bytearray()
            async for chunk in response.content.iter_chunked(1024 * 1024):
                body.extend(chunk)
                if len(body) > limit:
                    raise ValueError("Remote response exceeds configured size limit")
            return httpx.Response(
                response.status,
                headers=dict(response.headers),
                content=bytes(body),
                request=httpx.Request("GET", url),
            )
