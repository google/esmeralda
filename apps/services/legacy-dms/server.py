# Copyright 2025 Google LLC
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

"""Legacy Document Management System MCP server."""

from __future__ import annotations

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from otel_setup import init_telemetry, trace_tool

tracer = init_telemetry()

mcp = FastMCP(name="legacy-dms")

# ---------------------------------------------------------------------------
# Mock document store
# ---------------------------------------------------------------------------

_DOCUMENTS = {
    "DOC-2024-SM-1040": {
        "document_id": "DOC-2024-SM-1040",
        "title": "U.S. Individual Income Tax Return - Form 1040 (TY2024)",
        "tax_year": 2024,
        "applicants": "Julian & Elena Sterling",
        "filing_status": "Married Filing Jointly",
        "content": {
            "taxpayer_primary": {
                "name": "Julian A. Sterling",
                "ssn": "323-45-6789",
                "occupation": "Registered Nurse - ICU",
                "employer": "City General Hospital",
                "wages_salaries_tips": 88000,
            },
            "taxpayer_spouse": {
                "name": "Elena M. Sterling",
                "ssn": "321-54-9876",
                "occupation": "Senior Financial Analyst",
                "employer": "Acme Financial Services, Inc.",
                "wages_salaries_tips": 85000,
            },
            "income": {
                "total_wages": 173000,
                "taxable_interest": 3200,
                "ordinary_dividends": 5500,
                "capital_gains": 5000,
                "adjusted_gross_income": 186700,
            },
            "deductions": {
                "standard_deduction": 29200,
                "taxable_income": 157500,
            },
            "tax_summary": {
                "total_tax": 24750,
                "federal_tax_withheld": 27500,
                "refund_amount": 2750,
            },
        },
    },
    "DOC-2023-SM-1040": {
        "document_id": "DOC-2023-SM-1040",
        "title": "U.S. Individual Income Tax Return - Form 1040 (TY2023)",
        "tax_year": 2023,
        "applicants": "Julian & Elena Sterling",
        "filing_status": "Married Filing Jointly",
        "content": {
            "taxpayer_primary": {
                "name": "Julian A. Sterling",
                "ssn": "323-45-6789",
                "occupation": "Registered Nurse - ICU",
                "employer": "City General Hospital",
                "wages_salaries_tips": 85000,
            },
            "taxpayer_spouse": {
                "name": "Elena M. Sterling",
                "ssn": "321-54-9876",
                "occupation": "Financial Analyst",
                "employer": "Acme Financial Services, Inc.",
                "wages_salaries_tips": 68000,
            },
            "income": {
                "total_wages": 153000,
                "taxable_interest": 2800,
                "ordinary_dividends": 4100,
                "capital_gains": 2000,
                "adjusted_gross_income": 161900,
            },
            "deductions": {
                "standard_deduction": 27700,
                "taxable_income": 134200,
            },
            "tax_summary": {
                "total_tax": 20100,
                "federal_tax_withheld": 22800,
                "refund_amount": 2700,
            },
        },
    },
}

# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


# Synthetic borrower registry: maps a loan-file borrower ID to the applicant household, so a
# caller holding only the ID can resolve the applicants in one call instead of guessing names.
_BORROWERS = {
    "1001": {
        "borrower_id": "1001",
        "last_name": "Sterling",
        "applicants": [
            {"first_name": "Julian", "last_name": "Sterling", "role": "primary"},
            {"first_name": "Elena", "last_name": "Sterling", "role": "co-borrower"},
        ],
    },
}

# document_type values that match every stored type (the mock store only holds tax returns).
_WILDCARD_DOCUMENT_TYPES = {"", "all", "any", "*"}


def _find_borrower(applicant_last_name: str, borrower_id: str) -> dict | None:
    """Resolve a borrower by ID (preferred) or by last name."""
    if borrower_id:
        return _BORROWERS.get(borrower_id.strip().lstrip("#"))
    for borrower in _BORROWERS.values():
        if applicant_last_name.strip().lower() == borrower["last_name"].lower():
            return borrower
    return None


def _matches_type(document_type: str) -> bool:
    """Only tax returns exist in this mock store."""
    normalized = document_type.strip().lower()
    return normalized in _WILDCARD_DOCUMENT_TYPES or "tax" in normalized or "1040" in normalized


def _search_documents(
    applicant_last_name: str = "", document_type: str = "all", years: int = 2, borrower_id: str = ""
) -> dict:
    """Search for applicant documents and return matching metadata."""
    query = {
        "applicant_last_name": applicant_last_name,
        "borrower_id": borrower_id,
        "document_type": document_type,
        "years": years,
    }
    if not applicant_last_name.strip() and not borrower_id.strip():
        return {
            "status": "error",
            "error": "Provide applicant_last_name or borrower_id.",
            "query": query,
        }
    borrower = _find_borrower(applicant_last_name, borrower_id)
    if borrower and _matches_type(document_type):
        results = [
            {
                "document_id": "DOC-2024-SM-1040",
                "title": "U.S. Individual Income Tax Return - Form 1040 (TY2024)",
                "tax_year": 2024,
                "applicants": "Julian & Elena Sterling",
                "filing_status": "Married Filing Jointly",
                "date_filed": "2025-03-15",
            },
            {
                "document_id": "DOC-2023-SM-1040",
                "title": "U.S. Individual Income Tax Return - Form 1040 (TY2023)",
                "tax_year": 2023,
                "applicants": "Julian & Elena Sterling",
                "filing_status": "Married Filing Jointly",
                "date_filed": "2024-04-10",
            },
        ]
        return {
            "status": "success",
            "query": query,
            "borrower": borrower,
            "results": results[:years],
            "total_results": min(years, len(results)),
        }

    return {
        "status": "success",
        "query": query,
        "borrower": borrower,
        "results": [],
        "total_results": 0,
    }


def _get_document(document_id: str) -> dict:
    """Retrieve a full document by ID."""
    doc = _DOCUMENTS.get(document_id)
    if doc:
        return {"status": "success", "document": doc}

    return {
        "status": "error",
        "error": f"Document '{document_id}' not found in the system.",
    }


@mcp.tool()
def search_documents(
    applicant_last_name: str = "", document_type: str = "all", years: int = 2, borrower_id: str = ""
) -> dict:
    """Search the document management system for applicant documents.

    Pass either the applicant's last name or the loan-file borrower ID. The response includes the
    resolved borrower (ID and applicant first/last names), so no separate name lookup is needed.

    Args:
        applicant_last_name: Last name of the applicant. Optional when borrower_id is given.
        document_type: Type of document (e.g. 'tax_return', 'pay_stub', 'bank_statement'), or
            'all' (default) for every type.
        years: Number of years of documents to retrieve (default 2).
        borrower_id: Loan-file borrower ID (e.g. '1001'). Preferred when known.

    Returns:
        Dictionary with the resolved borrower and matching document metadata.
    """
    with trace_tool(tracer, "search_documents"):
        return _search_documents(applicant_last_name, document_type, years, borrower_id)


@mcp.tool()
def get_document(document_id: str) -> dict:
    """Retrieve a full document from the document management system by ID.

    Args:
        document_id: The unique document identifier (e.g. 'DOC-2024-SM-1040').

    Returns:
        Dictionary with the full document content.
    """
    with trace_tool(tracer, "get_document"):
        return _get_document(document_id)


# ---------------------------------------------------------------------------
# Starlette app with health endpoint + FastMCP mount
# ---------------------------------------------------------------------------


async def health(request):
    return JSONResponse({"status": "ok"})


_mcp_app = mcp.http_app(path="/mcp", stateless_http=True, json_response=True, host_origin_protection=False)

app = Starlette(
    routes=[
        Route("/health", health),
        Mount("/", app=_mcp_app),
    ],
    lifespan=_mcp_app.lifespan,
)
