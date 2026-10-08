"""MkDocs hook: render GitHub-flavoured Markdown the same way on the site.

The pages under docs/src are written to read well on github.com too, so at build time this hook:
  * converts GitHub alerts (``> [!NOTE]`` ...) into Material admonitions (``!!! note``);
  * inserts the blank line Python-Markdown needs before a list that follows a paragraph line;
  * widens 2-space nested lists to the 4 spaces Python-Markdown needs;
  * rewrites relative links that leave docs/src (``../../../infrastructure/...``, ``Makefile``)
    into links to the file on GitHub, since those files are not part of the site.
"""

from __future__ import annotations

import os
import re

REPO_URL = "https://github.com/google/esmeralda"
BRANCH = "main"

_ALERT_START = re.compile(r"^>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$", re.IGNORECASE)
_ALERT_TYPE = {
    "note": "note",
    "tip": "tip",
    "important": "info",
    "warning": "warning",
    "caution": "danger",
}
_LINK = re.compile(r"(\]\()(?!https?:|mailto:|#|/)([^)\s]+)(\))")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")


def _convert_alerts(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        match = _ALERT_START.match(lines[i])
        if not match:
            out.append(lines[i])
            i += 1
            continue
        kind = _ALERT_TYPE[match.group(1).lower()]
        i += 1
        body: list[str] = []
        while i < len(lines) and lines[i].startswith(">"):
            body.append(re.sub(r"^>\s?", "", lines[i]))
            i += 1
        out.append(f"!!! {kind}")
        out.append("")
        out.extend(f"    {line}" if line.strip() else "" for line in body)
        out.append("")
    return out


def _rewrite_links(line: str, page_dir: str, docs_dir: str, repo_root: str) -> str:
    def repl(match: re.Match[str]) -> str:
        target = match.group(2)
        path, sep, anchor = target.partition("#")
        absolute = os.path.normpath(os.path.join(page_dir, path))
        if absolute == docs_dir or absolute.startswith(docs_dir + os.sep):
            return match.group(0)  # a page or asset of the site: leave it to MkDocs
        rel = os.path.relpath(absolute, repo_root).replace(os.sep, "/")
        if rel.startswith(".."):
            return match.group(0)
        kind = "tree" if path.endswith("/") or os.path.isdir(absolute) else "blob"
        url = f"{REPO_URL}/{kind}/{BRANCH}/{rel}" + (f"#{anchor}" if sep else "")
        return f"{match.group(1)}{url}{match.group(3)}"

    return _LINK.sub(repl, line)


def _widen_nested_lists(lines: list[str]) -> list[str]:
    """GitHub nests list items indented by 2 spaces; Python-Markdown needs 4. Scale pages that use 2."""
    in_code = False
    two_space = False
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            in_code = not in_code
        elif not in_code and _LIST_ITEM.match(line):
            indent = len(line) - len(line.lstrip(" "))
            if indent % 4 == 2:
                two_space = True
                break
    if not two_space:
        return lines
    out: list[str] = []
    in_code = False
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            in_code = not in_code
        if not in_code and _LIST_ITEM.match(line) and line.startswith(" "):
            indent = len(line) - len(line.lstrip(" "))
            line = " " * (indent * 2) + line.lstrip(" ")
        out.append(line)
    return out


def _separate_lists(lines: list[str]) -> list[str]:
    """GitHub starts a list right after a paragraph line; Python-Markdown needs a blank line first."""
    out: list[str] = []
    in_code = False
    for line in lines:
        prefix_match = re.match(r"^((?:>\s?)*)(.*)$", line)
        prefix, body = prefix_match.group(1), prefix_match.group(2)
        if body.lstrip().startswith(("```", "~~~")):
            in_code = not in_code
        if not in_code and _LIST_ITEM.match(body) and out:
            prev = re.sub(r"^(?:>\s?)*", "", out[-1])
            prev_is_text = prev.strip() and not _LIST_ITEM.match(prev) and not prev.lstrip().startswith(("|", "#", "<"))
            # only top-level items after plain text (indented lines continue an existing list)
            if prev_is_text and not prev.startswith((" ", "\t")):
                out.append(prefix.rstrip())
        out.append(line)
    return out


def on_page_markdown(markdown, page, config, files):  # noqa: ARG001 (MkDocs hook signature)
    docs_dir = os.path.normpath(config["docs_dir"])
    repo_root = os.path.normpath(os.path.join(os.path.dirname(config["config_file_path"]), ".."))
    page_dir = os.path.dirname(os.path.normpath(page.file.abs_src_path or docs_dir))

    lines = markdown.split("\n")
    result: list[str] = []
    in_code = False
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            in_code = not in_code
        result.append(line if in_code else _rewrite_links(line, page_dir, docs_dir, repo_root))
    return "\n".join(_convert_alerts(_separate_lists(_widen_nested_lists(result))))
