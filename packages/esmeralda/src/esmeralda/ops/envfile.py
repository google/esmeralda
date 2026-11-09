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

"""User-local env file with the ``ESMERALDA_*`` variables Terragrunt reads.

``env.yaml`` reads ``ESMERALDA_BILLING_ACCOUNT`` (and optionally ``ESMERALDA_ORG_ID``) from the
environment. Instead of exporting them in an interactive shell profile, put them in a file
**outside the repository**: ``$ESMERALDA_ENV_FILE`` if set, else ``~/.config/esmeralda/env``::

    # ~/.config/esmeralda/env  (chmod 600; never commit it; plain KEY=VALUE, no quotes)
    ESMERALDA_BILLING_ACCOUNT=<billing-account-id>
    ESMERALDA_ORG_ID=<org-id>

Only ``ESMERALDA_*`` keys are read, and only when the variable is not already set in the
environment (the environment wins). Values are never printed.

The Makefile ``-include``s the same file, so write plain ``KEY=VALUE`` lines: this parser also
tolerates ``export`` and quotes, but make would keep the quotes (``doctor`` warns about them).
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

ENV_FILE_VAR = "ESMERALDA_ENV_FILE"
PREFIX = "ESMERALDA_"
DEFAULT_ENV_FILE = Path("~/.config/esmeralda/env")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def env_file_path(environ: Mapping[str, str] | None = None) -> Path:
    """``$ESMERALDA_ENV_FILE`` if set, else ``~/.config/esmeralda/env``."""
    environ = os.environ if environ is None else environ
    explicit = (environ.get(ENV_FILE_VAR) or "").strip()
    return Path(explicit).expanduser() if explicit else DEFAULT_ENV_FILE.expanduser()


def parse(text: str) -> dict[str, str]:
    """``KEY=VALUE`` lines: ``#`` comments, optional ``export`` prefix, optional quotes.

    Only ``ESMERALDA_*`` keys are returned; other keys and malformed lines are ignored.
    """
    return scan(text)[0]


def scan(text: str) -> tuple[dict[str, str], list[str], list[str]]:
    """``(values, quoted_keys, ignored_keys)``: the Makefile reads the same file, so quoted values and
    non-``ESMERALDA_*`` keys (which make would turn into make variables) are reported separately."""
    values: dict[str, str] = {}
    quoted: list[str] = []
    ignored: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not _KEY_RE.match(key):
            continue
        if not key.startswith(PREFIX) or key == ENV_FILE_VAR:
            ignored.append(key)
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
            quoted.append(key)
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[key] = value
    return values, quoted, ignored


@dataclass
class EnvFile:
    """The env file as found: path, parsed values (never print them) and permission problems."""

    path: Path
    exists: bool = False
    values: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    too_open: bool = False  # readable or writable by group/others
    quoted: list[str] = field(default_factory=list)  # keys with quoted values (make keeps the quotes)
    ignored: list[str] = field(default_factory=list)  # non-ESMERALDA_* keys (ignored here, read by make)

    def keys(self) -> list[str]:
        return sorted(self.values)


def read(environ: Mapping[str, str] | None = None) -> EnvFile:
    """Reads the env file (missing file: ``exists=False``, no error)."""
    path = env_file_path(environ)
    found = EnvFile(path)
    try:
        info = path.stat()
    except FileNotFoundError:
        return found
    except OSError as exc:
        found.error = f"cannot stat {path}: {exc.strerror or exc}"
        return found
    found.exists = True
    found.too_open = bool(stat.S_IMODE(info.st_mode) & (stat.S_IRWXG | stat.S_IRWXO))
    try:
        found.values, found.quoted, found.ignored = scan(path.read_text())
    except (OSError, UnicodeDecodeError) as exc:
        found.error = f"cannot read {path}: {exc}"
    return found


def apply(environ: MutableMapping[str, str], env_file: EnvFile | None = None) -> list[str]:
    """Sets the env file's variables that ``environ`` lacks (empty counts as unset); returns their names."""
    env_file = read(environ) if env_file is None else env_file
    loaded = []
    for key, value in env_file.values.items():
        if not (environ.get(key) or "").strip():
            environ[key] = value
            loaded.append(key)
    return sorted(loaded)


def source(key: str, environ: Mapping[str, str] | None = None, env_file: EnvFile | None = None) -> str:
    """Where ``key`` comes from: ``env``, ``env-file`` or ``missing``."""
    environ = os.environ if environ is None else environ
    if (environ.get(key) or "").strip():
        return "env"
    env_file = read(environ) if env_file is None else env_file
    if (env_file.values.get(key) or "").strip():
        return "env-file"
    return "missing"
