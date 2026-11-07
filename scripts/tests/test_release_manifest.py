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
"""Unit tests for scripts/release_manifest.py (stdlib unittest: `make test-release`)."""

import contextlib
import datetime as dt
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import release_manifest as rm  # noqa: E402

D1 = "sha256:" + "a" * 64
D2 = "sha256:" + "b" * 64
WHEN = dt.datetime(2026, 1, 31, 12, 34, 56, 789, tzinfo=dt.timezone.utc)


def _render(**overrides):
    kwargs = dict(
        version="v1.2.3",
        source_tag="dev-abc1234",
        source_commit="abc1234",
        promoted_at=WHEN,
        verified_env="dev",
        verified_date="2026-01-30",
        verified_result="PASS",
        images={"envoy-gateway": D1, "corporate-email": D2},
    )
    kwargs.update(overrides)
    return rm.render(**kwargs)


class RenderTest(unittest.TestCase):
    def test_round_trip(self):
        data = rm.parse(_render())
        self.assertEqual(data["schema_version"], "1")
        self.assertEqual(data["version"], "v1.2.3")
        self.assertEqual(data["source_tag"], "dev-abc1234")
        self.assertEqual(data["source_commit"], "abc1234")
        self.assertEqual(data["promoted_at"], "2026-01-31T12:34:56Z")
        self.assertEqual(data["repository"], "release")
        self.assertEqual(data["verified"], {"environment": "dev", "date": "2026-01-30", "result": "PASS"})
        self.assertEqual(data["images"], {"corporate-email": D2, "envoy-gateway": D1})

    def test_images_sorted_and_deterministic(self):
        text = _render(images={"zeta": D1, "alpha": D2})
        self.assertLess(text.index("  alpha:"), text.index("  zeta:"))
        self.assertEqual(text, _render(images={"alpha": D2, "zeta": D1}))

    def test_header_documents_schema(self):
        text = _render()
        self.assertTrue(text.startswith("# Release manifest"))
        for field in ("version:", "source_commit:", "promoted_at:", "verified:", "images:"):
            self.assertIn(field, text.split("\n\n", 1)[0])

    def test_valid_yaml_if_pyyaml_available(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        data = yaml.safe_load(_render())
        self.assertEqual(data["images"]["envoy-gateway"], D1)
        self.assertEqual(data["verified"]["result"], "PASS")

    def test_rejects_bad_fields(self):
        bad = [
            dict(version="1.2.3"),
            dict(version="v1.2"),
            dict(source_commit="not-a-sha"),
            dict(verified_date="31/01/2026"),
            dict(verified_date="2026-13-01"),
            dict(verified_result="FAIL"),
            dict(images={}),
            dict(images={"envoy-gateway": "sha256:short"}),
            dict(images={"Bad_Name": D1}),
            dict(promoted_at=dt.datetime(2026, 1, 31)),
        ]
        for override in bad:
            with self.subTest(override=override), self.assertRaises(rm.ManifestError):
                _render(**override)

    def test_prerelease_version_ok(self):
        self.assertIn('version: "v1.2.3-rc1"', _render(version="v1.2.3-rc1"))


class ParseImagesTest(unittest.TestCase):
    def test_pairs(self):
        self.assertEqual(rm.parse_images([f"a={D1}", f"b={D2}"]), {"a": D1, "b": D2})

    def test_errors(self):
        for pairs in ([], ["a"], [f"a={D1}", f"a={D2}"], ["a=sha256:xyz"]):
            with self.subTest(pairs=pairs), self.assertRaises(rm.ManifestError):
                rm.parse_images(pairs)


class CliTest(unittest.TestCase):
    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = rm.main(list(argv))
        return code, out.getvalue().strip()

    def test_write_then_get(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "release" / "prd.yaml")
            code, _ = self._run(
                "write", "--out", path, "--version", "v2.0.0", "--source-tag", "dev-1234567",
                "--source-commit", "1234567", "--verified-date", "2026-01-30",
                "--image", f"legacy-dms={D1}", "--image", f"envoy-gateway={D2}",
            )
            self.assertEqual(code, 0)
            self.assertEqual(self._run("get", "--manifest", path, "--key", "version"), (0, "v2.0.0"))
            self.assertEqual(self._run("get", "--manifest", path, "--key", "verified.result"), (0, "PASS"))
            self.assertEqual(self._run("get", "--manifest", path, "--image", "legacy-dms"), (0, D1))
            self.assertEqual(self._run("get", "--manifest", path, "--image", "missing")[0], 1)
            self.assertFalse(Path(path + ".tmp").exists())

    def test_get_missing_manifest(self):
        self.assertEqual(self._run("get", "--manifest", "/nonexistent/prd.yaml", "--key", "version")[0], 2)

    def test_write_rejects_bad_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prd.yaml"
            code, _ = self._run(
                "write", "--out", str(path), "--version", "latest", "--source-tag", "dev-1234567",
                "--source-commit", "1234567", "--verified-date", "2026-01-30", "--image", f"a={D1}",
            )
            self.assertEqual(code, 1)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
