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

"""Tests for mortgage agent error handling logic."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from agent.agent import _find_http_status_error, _handle_tool_error


def _make_http_status_error(status_code: int) -> httpx.HTTPStatusError:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    return httpx.HTTPStatusError("error", request=MagicMock(), response=response)


class TestFindHttpStatusError:
    def test_direct_match(self):
        exc = _make_http_status_error(403)
        assert _find_http_status_error(exc, 403) is True

    def test_wrong_status_code(self):
        exc = _make_http_status_error(500)
        assert _find_http_status_error(exc, 403) is False

    def test_chained_via_cause(self):
        inner = _make_http_status_error(403)
        outer = RuntimeError("wrapper")
        outer.__cause__ = inner
        assert _find_http_status_error(outer, 403) is True

    def test_chained_via_context(self):
        inner = _make_http_status_error(403)
        outer = RuntimeError("wrapper")
        outer.__context__ = inner
        assert _find_http_status_error(outer, 403) is True

    def test_exception_group(self):
        inner = _make_http_status_error(403)
        group = BaseExceptionGroup("group", [RuntimeError("other"), inner])
        assert _find_http_status_error(group, 403) is True

    def test_no_match(self):
        exc = RuntimeError("unrelated")
        assert _find_http_status_error(exc, 403) is False

    def test_nested_exception_group(self):
        inner = _make_http_status_error(403)
        inner_group = BaseExceptionGroup("inner", [inner])
        outer_group = BaseExceptionGroup("outer", [inner_group])
        assert _find_http_status_error(outer_group, 403) is True


class TestHandleToolError:
    def test_403_returns_friendly_message(self):
        tool = MagicMock()
        tool.name = "send_email"
        error = _make_http_status_error(403)
        result = _handle_tool_error(tool, {}, MagicMock(), error)
        assert result is not None
        assert "denied" in result["error"]
        assert "send_email" in result["error"]

    def test_non_403_returns_none(self):
        tool = MagicMock()
        tool.name = "read_email"
        error = _make_http_status_error(500)
        result = _handle_tool_error(tool, {}, MagicMock(), error)
        assert result is None

    def test_non_http_error_returns_none(self):
        tool = MagicMock()
        tool.name = "read_email"
        error = RuntimeError("something broke")
        result = _handle_tool_error(tool, {}, MagicMock(), error)
        assert result is None
