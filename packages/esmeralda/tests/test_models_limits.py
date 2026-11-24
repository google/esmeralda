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

import unittest

from esmeralda.models_limits import check_model_settings


class TestModelSettings(unittest.TestCase):
    def test_unknown_model_warns(self):
        with self.assertLogs("esmeralda.models_limits", level="WARNING") as logs:
            check_model_settings("unknown-model", location="us-central1")
        self.assertIn("unknown-model", logs.records[0].getMessage())

    def test_invalid_settings(self):
        for settings, message in (
            ({"thinking": {"thinking_level": "MINIMAL"}}, "thinking level"),
            ({"max_output_tokens": 65537}, "output tokens"),
            ({"location": "us-central1"}, "location"),
        ):
            with self.subTest(settings=settings), self.assertRaisesRegex(ValueError, message):
                check_model_settings("gemini-3.7-flash", **settings)

    def test_valid_settings(self):
        check_model_settings(
            "gemini-3.7-flash", thinking={"thinking_level": "MEDIUM"},
            max_output_tokens=65536, location="global",
        )
