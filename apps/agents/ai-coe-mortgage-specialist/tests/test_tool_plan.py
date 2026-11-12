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

"""Offline eval of the specialist's tool plan for the two sample prompts.

Each case holds the golden plan the instruction asks for (rounds of parallel tool calls). The
plan runs against the real MCP server logic (apps/services, synthetic data), which proves it is
reachable without guessing: every name and document ID used in a later round comes from an
earlier tool result or from the prompt. ``check_plan`` is also the rubric for a live trace
(e.g. the tool calls of ``esmeralda query``): no wrong-name searches, a bounded number of calls
and rounds, no duplicates, no inbox reads unless asked.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from agent.agent import _max_output_tokens, _thinking_config_kwargs
from agent.prompt import MORTGAGE_ASSISTANT_INSTRUCTION

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"


def _load_service(name: str, relpath: str) -> ModuleType:
    """Import a service module by path with telemetry stubbed (no GCP calls).

    The service's vendored ``caller_guard`` (standard library only) is imported from its own folder.
    """
    otel = MagicMock()
    guard_path = (SERVICES_DIR / relpath).parent / "caller_guard.py"
    guard_spec = importlib.util.spec_from_file_location("caller_guard", guard_path)
    guard = importlib.util.module_from_spec(guard_spec)
    with patch.dict(sys.modules, {"otel_setup": otel, "caller_guard": guard}):
        guard_spec.loader.exec_module(guard)
        spec = importlib.util.spec_from_file_location(name, SERVICES_DIR / relpath)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tools() -> dict:
    dms = _load_service("_plan_legacy_dms", "legacy-dms/server.py")
    income = _load_service("_plan_income", "income-verification/main.py")
    email = _load_service("_plan_email", "corporate-email/main.py")

    def verify(first_name: str, last_name: str) -> dict:
        return income._verify(first_name, last_name) or {"status": "error"}

    return {
        "dms_search_documents": dms._search_documents,
        "dms_get_document": dms._get_document,
        "income_verify_applicant": verify,
        "email_read_email": email._read,
    }


CASES = {
    "by_name": {
        "prompt": "Verify income for Julian Sterling and list his documents.",
        "known_names": {"julian", "sterling"},
        "max_calls": 5,
        "max_rounds": 2,
        "plan": [
            [
                ("dms_search_documents", {"applicant_last_name": "Sterling", "document_type": "all"}),
                ("income_verify_applicant", {"first_name": "Julian", "last_name": "Sterling"}),
            ],
            [
                ("income_verify_applicant", {"first_name": "Elena", "last_name": "Sterling"}),
                ("dms_get_document", {"document_id": "DOC-2024-SM-1040"}),
                ("dms_get_document", {"document_id": "DOC-2023-SM-1040"}),
            ],
        ],
    },
    "by_borrower_id": {
        "prompt": "Verify income for borrower ID 1001 and list their documents",
        "known_names": set(),
        "max_calls": 5,
        "max_rounds": 2,
        "plan": [
            [("dms_search_documents", {"borrower_id": "1001", "document_type": "all"})],
            [
                ("income_verify_applicant", {"first_name": "Julian", "last_name": "Sterling"}),
                ("income_verify_applicant", {"first_name": "Elena", "last_name": "Sterling"}),
                ("dms_get_document", {"document_id": "DOC-2024-SM-1040"}),
                ("dms_get_document", {"document_id": "DOC-2023-SM-1040"}),
            ],
        ],
    },
}

# The tool calls of the slow live trace for the borrower-ID prompt (before this change).
LIVE_TRACE_BEFORE = [
    [("email_read_email", {})],
    [("dms_search_documents", {"applicant_last_name": "Johnson", "document_type": "tax_return"})],
    [("dms_search_documents", {"applicant_last_name": "Smith", "document_type": "tax_return"})],
    [("dms_search_documents", {"applicant_last_name": "1001", "document_type": "tax_return"})],
    [("dms_search_documents", {"applicant_last_name": "Sterling", "document_type": "tax_return"})],
    [("dms_get_document", {"document_id": "DOC-2024-SM-1040"})],
    [("dms_get_document", {"document_id": "DOC-2023-SM-1040"})],
    [("income_verify_applicant", {"first_name": "Julian", "last_name": "Sterling"})],
    [("income_verify_applicant", {"first_name": "Elena", "last_name": "Sterling"})],
]


def check_plan(case: dict, rounds: list[list[tuple[str, dict]]], tools: dict | None = None) -> list[str]:
    """Return the rubric violations of a tool plan (empty when it passes).

    With ``tools``, the plan is executed round by round and names / document IDs must come from
    the prompt or an earlier result; without it, names must come from the prompt or the borrower
    registry (the Sterling household) and only the static rules are checked.
    """
    problems: list[str] = []
    calls = [call for rnd in rounds for call in rnd]
    if len(calls) > case["max_calls"]:
        problems.append(f"{len(calls)} tool calls > {case['max_calls']}")
    if len(rounds) > case["max_rounds"]:
        problems.append(f"{len(rounds)} rounds > {case['max_rounds']}")
    seen = set()
    for name, args in calls:
        key = (name, json.dumps(args, sort_keys=True))
        if key in seen:
            problems.append(f"duplicate call {name}({args})")
        seen.add(key)
        if name.startswith("email_") and "email" not in case["prompt"].lower():
            problems.append(f"{name} not requested by the prompt")

    known_names = set(case["known_names"]) if tools else set(case["known_names"]) | {"sterling", "julian", "elena"}
    known_docs: set[str] = set()
    for rnd in rounds:
        results = []
        for name, args in rnd:
            last = args.get("applicant_last_name", "")
            if last and last.lower() not in known_names:
                problems.append(f"{name} guessed last name {last!r}")
            for key in ("first_name", "last_name"):
                if key in args and args[key].lower() not in known_names:
                    problems.append(f"{name} guessed {key} {args[key]!r}")
            doc = args.get("document_id")
            if tools and doc and doc not in known_docs:
                problems.append(f"{name} used document {doc!r} not returned by a search")
            if tools:
                results.append(tools[name](**args))
        # Results become known only after the whole (parallel) round returns.
        for result in results:
            borrower = result.get("borrower") or {}
            for applicant in borrower.get("applicants", []):
                known_names.update({applicant["first_name"].lower(), applicant["last_name"].lower()})
            known_docs.update(doc["document_id"] for doc in result.get("results", []))
            if result.get("status") == "error":
                problems.append(f"tool error: {result}")
    return problems


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_golden_plan_passes_rubric_against_servers(case_id, tools):
    case = CASES[case_id]
    assert check_plan(case, case["plan"], tools) == []


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_golden_plan_covers_the_request(case_id, tools):
    calls = [call for rnd in CASES[case_id]["plan"] for call in rnd]
    verified = {(a["first_name"], a["last_name"]) for n, a in calls if n == "income_verify_applicant"}
    assert verified == {("Julian", "Sterling"), ("Elena", "Sterling")}
    searches = [a for n, a in calls if n == "dms_search_documents"]
    assert len(searches) == 1


def test_borrower_id_resolves_without_a_name(tools):
    result = tools["dms_search_documents"](borrower_id="1001", document_type="all")
    names = {(a["first_name"], a["last_name"]) for a in result["borrower"]["applicants"]}
    assert ("Julian", "Sterling") in names
    assert result["total_results"] == 2


def test_live_trace_before_fails_rubric(tools):
    problems = check_plan(CASES["by_borrower_id"], LIVE_TRACE_BEFORE, tools)
    joined = "\n".join(problems)
    assert "guessed last name 'Johnson'" in joined
    assert "guessed last name 'Smith'" in joined
    assert "guessed last name '1001'" in joined
    assert "email_read_email not requested" in joined
    assert "tool calls >" in joined


class TestInstruction:
    def test_answer_format_is_short_and_structured(self):
        assert "at most about 200 words" in MORTGAGE_ASSISTANT_INSTRUCTION
        for section in ("**Applicants:**", "**Verified income**", "**Tax returns**", "**Discrepancies:**"):
            assert section in MORTGAGE_ASSISTANT_INSTRUCTION
        assert "Never copy whole documents or raw tool output" in MORTGAGE_ASSISTANT_INSTRUCTION
    def test_resolves_borrower_id_through_dms_search(self):
        assert "borrower_id" in MORTGAGE_ASSISTANT_INSTRUCTION
        assert "only source that maps a borrower ID" in MORTGAGE_ASSISTANT_INSTRUCTION

    def test_forbids_guessing_and_repeats(self):
        assert "NEVER guess applicant names" in MORTGAGE_ASSISTANT_INSTRUCTION
        assert "Never repeat a tool call" in MORTGAGE_ASSISTANT_INSTRUCTION

    def test_asks_for_parallel_calls(self):
        assert "in parallel" in MORTGAGE_ASSISTANT_INSTRUCTION

    def test_inbox_only_when_asked(self):
        assert "only read it when the request is about email" in MORTGAGE_ASSISTANT_INSTRUCTION

    def test_keeps_existing_rules(self):
        for rule in ("NEVER fabricate", "cite which tool", "report the error honestly", "SSN"):
            assert rule in MORTGAGE_ASSISTANT_INSTRUCTION


class TestThinkingConfig:
    def test_level(self):
        assert _thinking_config_kwargs("low") == {"thinking_level": "LOW"}
        assert _thinking_config_kwargs(" High ") == {"thinking_level": "HIGH"}
        assert _thinking_config_kwargs("minimal") == {"thinking_level": "MINIMAL"}  # live-test option

    def test_budget(self):
        assert _thinking_config_kwargs("1024") == {"thinking_budget": 1024}
        assert _thinking_config_kwargs("-1") == {"thinking_budget": -1}

    def test_empty_keeps_model_default(self):
        assert _thinking_config_kwargs("") == {}

    def test_invalid(self):
        with pytest.raises(ValueError):
            _thinking_config_kwargs("fast")


class TestMaxOutputTokens:
    def test_value(self):
        assert _max_output_tokens("2048") == 2048

    def test_empty_or_zero_keeps_model_default(self):
        assert _max_output_tokens("") is None
        assert _max_output_tokens("0") is None


# An answer in the instruction's format for the borrower-ID prompt, built from the synthetic tool
# results. It shows the format carries every required fact well under the ~400-token target.
GOLDEN_ANSWER = """\
**Applicants:**
- Julian A. Sterling, borrower 1001, SSN [US_SOCIAL_SECURITY_NUMBER]
- Elena M. Sterling, borrower 1001, SSN [US_SOCIAL_SECURITY_NUMBER]

**Verified income** (income_verify_applicant):
- Julian: City General Hospital, Registered Nurse - ICU, Active - Full Time, $92,000
- Elena: Summit Advisory Group, Director of Financial Planning, Active - Full Time, $98,000
- Combined $190,000; trend INCREASING; confidence HIGH

**Tax returns** (dms_get_document):
- TY2024: Julian $88,000, Elena $85,000, AGI $186,700
- TY2023: Julian $85,000, Elena $68,000, AGI $161,900

**Discrepancies:**
- Elena's employer changed: Acme Financial Services (tax returns) vs. Summit Advisory Group (since 2025-11-15).
- Julian's verified salary ($92,000) is above his TY2024 wages ($88,000).

**Documents** (dms_search_documents):
- DOC-2024-SM-1040, Form 1040, 2024
- DOC-2023-SM-1040, Form 1040, 2023
"""


def check_answer(answer: str, max_words: int = 220) -> list[str]:
    """Rubric for a final answer to the sample prompts (also usable on a live answer)."""
    problems = []
    words = len(answer.split())
    if words > max_words:
        problems.append(f"{words} words > {max_words}")
    for fact in ("Julian", "Elena", "SSN", "92,000", "98,000", "190,000", "DOC-2024-SM-1040", "DOC-2023-SM-1040"):
        if fact not in answer:
            problems.append(f"missing {fact!r}")
    if '"content"' in answer or "taxpayer_primary" in answer:
        problems.append("raw tool output copied into the answer")
    return problems


def test_golden_answer_fits_budget_with_required_facts():
    assert check_answer(GOLDEN_ANSWER) == []
