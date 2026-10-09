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

"""``esmeralda`` command line.

``esmeralda run -- <command> [args...]`` is the container entrypoint: it installs the Agent Gateway
root CA, then replaces itself with the server command (``exec``), so the server keeps PID 1
and receives signals directly.
"""

from __future__ import annotations

import logging
import os
import sys

from esmeralda import certs

USAGE = "usage: esmeralda run -- <command> [args...]"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] != "run":
        print(USAGE, file=sys.stderr)
        return 2
    command = args[1:]
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print(USAGE, file=sys.stderr)
        return 2

    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        installed = certs.install_gateway_ca()
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    if installed:
        print(f"✅ Installed {installed} Agent Gateway root certificate(s) from {certs.ENV_VAR}", flush=True)

    sys.stdout.flush()
    os.execvp(command[0], command)
    return 0  # unreachable: execvp only returns by raising


if __name__ == "__main__":
    sys.exit(main())
