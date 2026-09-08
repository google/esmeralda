# Copyright 2026 Google LLC
# Site-level initialization: enforce IPv4, PSC VIP routing, and aiohttp/httpx compatibility
import os
import socket

PSC_GAPIS_IP = os.environ.get("GOOGLE_APIS_PSC_IP")

# 1. Force IPv4 and rewrite *.googleapis.com in socket.getaddrinfo (only if GOOGLE_APIS_PSC_IP is set)
_orig_getaddrinfo = socket.getaddrinfo

def _custom_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if family == 0 or family == socket.AF_UNSPEC:
        family = socket.AF_INET
    if PSC_GAPIS_IP and isinstance(host, str) and (host.endswith(".googleapis.com") or host == "googleapis.com"):
        host = PSC_GAPIS_IP
    return _orig_getaddrinfo(host, port, family, type, proto, flags)

socket.getaddrinfo = _custom_getaddrinfo

# 2. Patch asyncio getaddrinfo
try:
    import asyncio.base_events
    _orig_asyncio_getaddrinfo = asyncio.base_events.BaseEventLoop.getaddrinfo
    async def _custom_asyncio_getaddrinfo(self, host, port, *args, **kwargs):
        if PSC_GAPIS_IP and isinstance(host, str) and (host.endswith(".googleapis.com") or host == "googleapis.com"):
            host = PSC_GAPIS_IP
        return await _orig_asyncio_getaddrinfo(self, host, port, *args, **kwargs)
    asyncio.base_events.BaseEventLoop.getaddrinfo = _custom_asyncio_getaddrinfo
except Exception:
    pass

# 3. Patch aiohttp TCPConnector._resolve_host
try:
    import aiohttp.connector
    _orig_resolve_host = aiohttp.connector.TCPConnector._resolve_host
    async def _custom_resolve_host(self, host, port, traces=None):
        if PSC_GAPIS_IP and isinstance(host, str) and (host.endswith(".googleapis.com") or host == "googleapis.com"):
            return [
                {
                    "hostname": host,
                    "host": PSC_GAPIS_IP,
                    "port": port,
                    "family": socket.AF_INET,
                    "proto": 6,
                    "flags": 0,
                }
            ]
        return await _orig_resolve_host(self, host, port, traces)
    aiohttp.connector.TCPConnector._resolve_host = _custom_resolve_host
except Exception:
    pass

# 4. Fix HTTPS_PROXY scheme if set to https:// to prevent urllib3 SSLEOFError
if "HTTPS_PROXY" in os.environ and os.environ["HTTPS_PROXY"].startswith("https://"):
    os.environ["HTTPS_PROXY"] = "http://" + os.environ["HTTPS_PROXY"][8:]
if "https_proxy" in os.environ and os.environ["https_proxy"].startswith("https://"):
    os.environ["https_proxy"] = "http://" + os.environ["https_proxy"][8:]

try:
    import urllib3.poolmanager
    _orig_proxy_from_url = urllib3.poolmanager.proxy_from_url
    def _patched_proxy_from_url(url, **kw):
        if url.startswith("https://"):
            url = "http://" + url[8:]
        return _orig_proxy_from_url(url, **kw)
    urllib3.poolmanager.proxy_from_url = _patched_proxy_from_url
except ImportError:
    pass

# 5. Only enable trust_env if explicit proxy is configured
try:
    import aiohttp
    _orig_init = aiohttp.ClientSession.__init__
    def _patched_init(self, *args, **kwargs):
        if "HTTP_PROXY" in os.environ or "HTTPS_PROXY" in os.environ:
            kwargs.setdefault("trust_env", True)
        _orig_init(self, *args, **kwargs)
    aiohttp.ClientSession.__init__ = _patched_init
except ImportError:
    pass


