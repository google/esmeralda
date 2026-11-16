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

"""Running the BFF: in the container (``python -m esmeralda_ui``) or locally (``esmeralda ui --local``)."""

from __future__ import annotations

import logging
import os

from esmeralda_ui.settings import Settings, SettingsError

LOOPBACK = "127.0.0.1"


def serve(settings: Settings, *, host: str, port: int) -> None:
    import uvicorn

    from esmeralda_ui.app import create_app

    if settings.local and host != LOOPBACK:
        raise SettingsError("local mode only binds to 127.0.0.1 (its identity is not verified)")
    uvicorn.run(
        create_app(settings),
        host=host,
        port=port,
        server_header=False,
        proxy_headers=False,
        log_level="info",
        access_log=True,
    )


def main() -> int:
    """Container entrypoint: cloud mode from ``ESMERALDA_UI_*``, listening on ``$PORT`` (default 8080)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = Settings.from_env()
    except SettingsError as exc:
        logging.getLogger(__name__).error("invalid configuration: %s", exc)
        return 2
    host = LOOPBACK if settings.local else "0.0.0.0"  # noqa: S104 - the container listens on all interfaces
    serve(settings, host=host, port=int(os.environ.get("PORT", "8080")))
    return 0
