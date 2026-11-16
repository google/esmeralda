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

"""The API contract: ``apps/frontends/esmeralda-ui/openapi.json``, generated from the FastAPI app.

``python -m esmeralda_ui.openapi`` rewrites it; ``--check`` exits 1 when the committed file is stale.
The web generates its TypeScript types from that file (``npm run gen:types``), and its test suite
fails when ``src/api/schema.ts`` is stale, so a field can't exist on one side only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parents[3]
OPENAPI_PATH = APP_DIR / "openapi.json"


def schema() -> dict[str, Any]:
    from esmeralda_ui.app import create_app
    from esmeralda_ui.settings import Settings

    return create_app(Settings(mode="local", demo=True)).openapi()


def render() -> str:
    return json.dumps(schema(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m esmeralda_ui.openapi", description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the committed openapi.json is stale")
    parser.add_argument("--path", type=Path, default=OPENAPI_PATH, help=f"output file (default: {OPENAPI_PATH})")
    args = parser.parse_args(argv)
    expected = render()
    if args.check:
        current = args.path.read_text() if args.path.is_file() else ""
        if current != expected:
            print(f"❌ {args.path} is stale: run `python -m esmeralda_ui.openapi`", file=sys.stderr)
            return 1
        print(f"✅ {args.path} is up to date")
        return 0
    args.path.write_text(expected)
    print(f"✅ Wrote {args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
