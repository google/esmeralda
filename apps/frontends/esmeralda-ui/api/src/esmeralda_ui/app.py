# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The FastAPI application: read-only ``/api/*`` endpoints plus the built web bundle.

``create_app(settings)`` wires the middleware (security headers, request guard, IAP identity), the
catalog (``esmeralda.catalog``) and the data source (BigQuery, explicit demo, or unconfigured).
Errors are generic (``{"error": "..."}``); details go to the log only. There is no docs UI and no
``/openapi.json`` route: the contract is the committed ``openapi.json``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from esmeralda_ui import __version__
from esmeralda_ui.auth import Authenticator, Identity, KeyProvider
from esmeralda_ui.catalog_service import CatalogService, CatalogUnavailable
from esmeralda_ui.data import DataSource, DataUnavailable, data_source
from esmeralda_ui.models import (
    Audit,
    Catalog,
    CatalogTarget,
    ErrorBody,
    Health,
    Me,
    Trace,
    TraceList,
    Usage,
)
from esmeralda_ui.security import AuthGate, RequestGuard, SecurityHeaders
from esmeralda_ui.settings import Settings

log = logging.getLogger(__name__)

TRACE_ID_PATTERN = r"^[0-9a-f]{32}$"
TARGET_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}$"
TEAM_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"
MONTH_PATTERN = r"^[0-9]{4}-(0[1-9]|1[0-2])$"
TRACE_ID_RE = re.compile(TRACE_ID_PATTERN)

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorBody, "description": "Missing or invalid IAP identity"},
    503: {"model": ErrorBody, "description": "Data source unavailable or not configured"},
}


def _identity(request: Request) -> Identity:
    identity = getattr(request.state, "identity", None)
    if not isinstance(identity, Identity):  # AuthGate guarantees it; never serve without one
        raise HTTPException(status_code=401, detail="unauthenticated")
    return identity


def create_app(
    settings: Settings,
    *,
    keys: KeyProvider | None = None,
    bigquery_client_factory: Callable[[], Any] | None = None,
    source: DataSource | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Esmeralda console API",
        version="1",
        summary="Read-only BFF of the Esmeralda console",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    catalog = CatalogService(settings.catalog_root, settings.environment)
    data = source or data_source(settings, bigquery_client_factory)

    # Outermost first: headers on every response, then method/body limits, then identity.
    app.add_middleware(AuthGate, authenticator=Authenticator(settings, keys))
    app.add_middleware(RequestGuard)
    app.add_middleware(SecurityHeaders, hsts=not settings.local)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) and exc.status_code < 500 else "unavailable"
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"error": "invalid request parameters"}, status_code=422)

    @app.exception_handler(CatalogUnavailable)
    async def _catalog_error(_: Request, exc: CatalogUnavailable) -> JSONResponse:
        log.error("catalog unavailable: %s", exc)
        return JSONResponse({"error": "catalog unavailable"}, status_code=503)

    @app.exception_handler(DataUnavailable)
    async def _data_error(_: Request, exc: DataUnavailable) -> JSONResponse:
        log.error("data unavailable: %s", exc)
        return JSONResponse({"error": "data source unavailable"}, status_code=503)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return JSONResponse({"error": "internal error"}, status_code=500)

    @app.get("/healthz", response_model=Health, tags=["ops"])
    def healthz() -> Health:
        return Health(status="ok")

    @app.get("/api/me", response_model=Me, responses=_ERRORS, tags=["identity"])
    def me(request: Request) -> Me:
        identity = _identity(request)
        return Me(
            email=identity.email,
            subject=identity.subject,
            auth=identity.auth,  # type: ignore[arg-type]
            environment=settings.environment,
            demo=settings.demo,
            data_source=settings.data_source,  # type: ignore[arg-type]
        )

    @app.get("/api/catalog", response_model=Catalog, responses=_ERRORS, tags=["catalog"])
    def get_catalog(request: Request) -> Catalog:
        _identity(request)
        return catalog.get()

    @app.get(
        "/api/catalog/{target}",
        response_model=CatalogTarget,
        responses={**_ERRORS, 404: {"model": ErrorBody, "description": "Unknown target"}},
        tags=["catalog"],
    )
    def get_target(request: Request, target: Annotated[str, PathParam(pattern=TARGET_PATTERN)]) -> CatalogTarget:
        _identity(request)
        found = catalog.target(target)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown target")
        return found

    @app.get("/api/usage", response_model=Usage, responses=_ERRORS, tags=["finops"])
    def get_usage(
        request: Request,
        month: Annotated[
            str | None, Query(pattern=MONTH_PATTERN, description="YYYY-MM (default: this month, UTC)")
        ] = None,
        team: Annotated[str | None, Query(pattern=TEAM_PATTERN, description="Only this billed team")] = None,
    ) -> Usage:
        _identity(request)
        return data.usage(month or datetime.now(UTC).strftime("%Y-%m"), team)

    @app.get("/api/audit", response_model=Audit, responses=_ERRORS, tags=["finops"])
    def get_audit(
        request: Request,
        days: Annotated[int, Query(ge=1, le=90)] = 7,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> Audit:
        _identity(request)
        return data.audit(days, limit)

    @app.get("/api/traces", response_model=TraceList, responses=_ERRORS, tags=["traces"])
    def list_traces(
        request: Request,
        days: Annotated[int, Query(ge=1, le=31)] = 1,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> TraceList:
        _identity(request)
        return data.traces(days, limit)

    @app.get(
        "/api/traces/{trace_id}",
        response_model=Trace,
        responses={**_ERRORS, 404: {"model": ErrorBody, "description": "No usage records for this trace"}},
        tags=["traces"],
    )
    def get_trace(
        request: Request,
        trace_id: Annotated[str, PathParam(pattern=TRACE_ID_PATTERN, description="W3C trace id, 32 lowercase hex")],
        days: Annotated[int, Query(ge=1, le=31)] = 7,
    ) -> Trace:
        _identity(request)
        if not TRACE_ID_RE.fullmatch(trace_id):  # belt and braces: never reaches a query unvalidated
            raise HTTPException(status_code=422, detail="invalid trace id")
        trace = data.trace(trace_id, days)
        if trace is None:
            raise HTTPException(status_code=404, detail="trace not found")
        return trace

    @app.get("/api/{rest:path}", include_in_schema=False)
    def api_not_found(rest: str) -> JSONResponse:
        return JSONResponse({"error": "not found"}, status_code=404)

    static_dir = settings.static_dir
    if static_dir and Path(static_dir, "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    else:

        @app.get("/", include_in_schema=False)
        def no_web() -> JSONResponse:
            return JSONResponse({"error": "web bundle not built"}, status_code=404)

    app.state.version = __version__
    return app
