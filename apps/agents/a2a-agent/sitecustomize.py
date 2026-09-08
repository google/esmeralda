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
os.environ.setdefault("GRPC_DNS_RESOLVER", "native")
if "ALL_PROXY" in os.environ:
    _all_p = os.environ["ALL_PROXY"]
    if _all_p.startswith("https://"):
        _all_p = "http://" + _all_p[8:]
    if "HTTP_PROXY" not in os.environ:
        os.environ["HTTP_PROXY"] = _all_p
    if "HTTPS_PROXY" not in os.environ:
        os.environ["HTTPS_PROXY"] = _all_p

if "HTTP_PROXY" in os.environ and "HTTPS_PROXY" not in os.environ:
    os.environ["HTTPS_PROXY"] = os.environ["HTTP_PROXY"]
if "http_proxy" in os.environ and "https_proxy" not in os.environ:
    os.environ["https_proxy"] = os.environ["http_proxy"]

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

# 5. aiohttp session and request proxy enforcement
try:
    import aiohttp
    import aiohttp.connector
    from urllib.parse import urlparse

    def _get_env_proxy(url_str):
        if not url_str:
            return None
        try:
            parsed = urlparse(str(url_str))
            scheme = parsed.scheme.lower()
            host = (parsed.hostname or "").lower()

            no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
            no_proxy_hosts = [h.strip().lower() for h in no_proxy.split(",") if h.strip()]
            for np in no_proxy_hosts:
                if host == np or host.endswith("." + np.lstrip(".")):
                    return None

            if scheme == "https":
                return os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
            elif scheme == "http":
                return os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
            return os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        except Exception:
            return os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")

    # Patch ClientSession.__init__
    _orig_session_init = aiohttp.ClientSession.__init__
    def _patched_session_init(self, *args, **kwargs):
        if os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("http_proxy") or os.environ.get("https_proxy"):
            kwargs["trust_env"] = True
        _orig_session_init(self, *args, **kwargs)
    aiohttp.ClientSession.__init__ = _patched_session_init

    # Patch ClientSession._request to ensure proxy is attached even if session defaults were bypassed
    _orig_session_request = aiohttp.ClientSession._request
    async def _patched_session_request(self, method, str_or_url, *args, **kwargs):
        if kwargs.get("proxy") is None:
            proxy = _get_env_proxy(str_or_url)
            if proxy:
                kwargs["proxy"] = proxy
        return await _orig_session_request(self, method, str_or_url, *args, **kwargs)
    aiohttp.ClientSession._request = _patched_session_request

    # Patch TCPConnector.__init__
    _orig_connector_init = aiohttp.connector.TCPConnector.__init__
    def _patched_connector_init(self, *args, **kwargs):
        if os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("http_proxy") or os.environ.get("https_proxy"):
            kwargs.setdefault("trust_env", True)
        _orig_connector_init(self, *args, **kwargs)
    aiohttp.connector.TCPConnector.__init__ = _patched_connector_init
except ImportError:
    pass




