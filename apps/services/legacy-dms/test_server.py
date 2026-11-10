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

from unittest.mock import MagicMock, patch

# Patch telemetry before importing server to avoid GCP API calls during testing.
with patch("otel_setup.init_telemetry", return_value=MagicMock()):
    from server import _get_document, _search_documents


def test_search_by_name_and_doc_type():
    result = _search_documents(applicant_last_name="Sterling", document_type="tax_return")
    assert result["status"] == "success"
    assert result["total_results"] > 0


def test_search_unknown_applicant():
    result = _search_documents(applicant_last_name="Unknown", document_type="tax_return")
    assert result["status"] == "success"
    assert result["total_results"] == 0


def test_get_document_by_id():
    result = _get_document(document_id="DOC-2024-SM-1040")
    assert result["status"] == "success"
    assert result["document"]["document_id"] == "DOC-2024-SM-1040"


def test_get_document_not_found():
    result = _get_document(document_id="DOC-NONEXISTENT")
    assert result["status"] == "error"


def test_search_by_borrower_id_resolves_applicants():
    result = _search_documents(borrower_id="1001")
    assert result["status"] == "success"
    assert result["total_results"] == 2
    assert result["borrower"]["last_name"] == "Sterling"
    names = {(a["first_name"], a["last_name"]) for a in result["borrower"]["applicants"]}
    assert names == {("Julian", "Sterling"), ("Elena", "Sterling")}


def test_search_by_name_returns_borrower_id():
    result = _search_documents(applicant_last_name="sterling", document_type="all")
    assert result["borrower"]["borrower_id"] == "1001"
    assert result["total_results"] == 2


def test_search_unknown_borrower_id():
    result = _search_documents(borrower_id="9999")
    assert result["status"] == "success"
    assert result["borrower"] is None
    assert result["total_results"] == 0


def test_search_requires_name_or_borrower_id():
    result = _search_documents()
    assert result["status"] == "error"


def test_search_non_tax_type_returns_no_documents():
    result = _search_documents(applicant_last_name="Sterling", document_type="pay_stub")
    assert result["total_results"] == 0
    assert result["borrower"]["borrower_id"] == "1001"


def test_tools_json_matches_search_signature():
    import inspect
    import json
    from pathlib import Path

    spec = json.loads((Path(__file__).parent / "tools.json").read_text())
    tool = next(t for t in spec["tools"] if t["name"] == "search_documents")
    assert set(tool["inputSchema"]["properties"]) == set(inspect.signature(_search_documents).parameters)
    assert "required" not in tool["inputSchema"]
