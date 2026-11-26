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

"""Human-readable formatters for ``esmeralda catalog show`` (``table`` and ``tree``)."""

from __future__ import annotations

import os
import sys
from typing import Any

MAX_WIDTH = 120
_CALLER_HEADERS = ("caller", "identity", "via", "pays (team / cost centers)", "extras")


def use_color(color: bool | None = None) -> bool:
    """Whether ANSI color codes should be emitted (respecting ``NO_COLOR`` and non-TTY stdout)."""
    if color is not None:
        return color
    if os.environ.get("NO_COLOR"):
        return False
    return bool(hasattr(sys.stdout, "isatty") and sys.stdout.isatty())


def _paint(text: str, code: str, *, color: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if color and text else text


def _pad(plain: str, width: int, styled: str | None = None) -> str:
    rendered = styled if styled is not None else plain
    gap = max(0, width - len(plain))
    return rendered + (" " * gap)


def _short_registry_note(registry: str) -> str:
    if registry.startswith("Google API entry"):
        return "Google API entry (agents opt in via google_apis)"
    if "adopted:" in registry:
        group = registry.split(" (", 1)[0]
        return f"{group} (adopted engine URLs, per-target)"
    if registry.endswith(" (per-target caller list)"):
        group = registry[: -len(" (per-target caller list)")]
        return f"{group} (per-target)"
    return registry


def _format_extra_value(key: str, value: Any) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    if isinstance(value, dict):
        if key == "quota":
            parts = []
            for k, v in value.items():
                if k == "tokens_per_minute":
                    parts.append(f"{v}tpm")
                elif k == "requests_per_minute":
                    parts.append(f"{v}rpm")
                else:
                    parts.append(f"{k}={v}")
            return ",".join(parts)
        return ",".join(f"{k}={v}" for k, v in value.items())
    return str(value)


def _route_segment(target: dict[str, Any], *, color: bool) -> tuple[str, str, bool]:
    gateway = target.get("gateway") or "-"
    ingress = target.get("ingress_backend")
    if ingress is not None and ingress != gateway:
        plain = f"⚠️ route: gateway={gateway} != ingress={ingress}"
        return plain, _paint(plain, "33;1", color=color), True
    plain = f"route: {gateway}"
    styled = _paint("route: ", "2", color=color) + gateway
    return plain, styled, False


def _badge_segments(target: dict[str, Any], *, color: bool) -> list[tuple[str, str]]:
    badges: list[tuple[str, str]] = []
    if target.get("example"):
        badges.append(("[example]", _paint("[example]", "36", color=color)))
    if not target.get("compiled") and not target.get("registry"):
        badges.append(("[not compiled]", _paint("[not compiled]", "33", color=color)))
    if target.get("adopted_from"):
        text = f"[adopted from {target['adopted_from']}]"
        badges.append((text, _paint(text, "36", color=color)))
    return badges


def _target_header(target: dict[str, Any], *, color: bool) -> str:
    name = _paint(target["target"], "1", color=color)
    sep = _paint(" · ", "2", color=color)
    kind = _paint(target["kind"], "36", color=color)
    owner = _paint("owner ", "2", color=color) + target["owner_team"]
    return f"🎯 {name}{sep}{kind}{sep}{owner}"


def _detail_segments(target: dict[str, Any], *, color: bool) -> tuple[list[tuple[str, str]], bool]:
    segments: list[tuple[str, str]] = []
    if target.get("deploy"):
        dep = str(target["deploy"])
        segments.append((dep, dep))
    route_plain, route_styled, mismatch = _route_segment(target, color=color)
    segments.append((route_plain, route_styled))
    if target.get("registry") and not target.get("compiled"):
        note = _short_registry_note(str(target["registry"]))
        segments.append((f"registry: {note}", _paint("registry: ", "2", color=color) + note))
    badges = _badge_segments(target, color=color)
    if badges:
        segments.append((" ".join(p for p, _ in badges), " ".join(s for _, s in badges)))
    return segments, mismatch


def _pack_segments(
    segments: list[tuple[str, str]],
    indent: str,
    *,
    color: bool,
    max_width: int = MAX_WIDTH,
) -> list[str]:
    if not segments:
        return []
    plain_sep = " · "
    styled_sep = _paint(plain_sep, "2", color=color)
    lines: list[str] = []
    cur_plain = ""
    cur_styled = ""
    for plain, styled in segments:
        if not cur_plain:
            cur_plain = indent + plain
            cur_styled = indent + styled
        elif len(cur_plain) + len(plain_sep) + len(plain) <= max_width:
            cur_plain += plain_sep + plain
            cur_styled += styled_sep + styled
        else:
            lines.append(cur_styled)
            cur_plain = indent + plain
            cur_styled = indent + styled
    if cur_plain:
        lines.append(cur_styled)
    return lines


def format_provenance(prov: dict[str, Any], *, indent: str = "   ", color: bool = False) -> list[str]:
    """Render target or team provenance lines."""
    lines: list[str] = []
    entry = prov.get("entry") or {}
    if entry:
        text = f"{indent}↳ from {entry.get('source')} ({entry.get('origin')})"
        lines.append(_paint(text, "2", color=color))
    for path, record in (prov.get("fields") or {}).items():
        shadowed = record.get("shadowed") or []
        losers = f"; shadows {', '.join(s['source'] for s in shadowed)}" if shadowed else ""
        where = f"{record.get('origin')}, {record.get('strategy', '-')}{losers}"
        text = f"{indent}  {path}: {record.get('source')} ({where})"
        lines.append(_paint(text, "2", color=color))
    return lines


def _caller_extras(caller: dict[str, Any], *, color: bool) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for key in ("product", "tools", "quota"):
        if caller.get(key):
            val = _format_extra_value(key, caller[key])
            tok = f"{key}={val}"
            items.append((tok, tok))
    if caller.get("enforced"):
        items.append(("[enforced]", _paint("[enforced]", "32", color=color)))
    else:
        items.append(("[declared only]", _paint("[declared only]", "33", color=color)))
    return items


def _split_long_token(plain: str, styled: str, budget: int) -> list[tuple[str, str]]:
    if len(plain) <= budget or "," not in plain or plain != styled:
        return [(plain, styled)]
    out: list[tuple[str, str]] = []
    cur = ""
    parts = plain.split(",")
    for idx, part in enumerate(parts):
        piece = part if idx == len(parts) - 1 else f"{part},"
        if not cur or len(cur) + len(piece) <= budget:
            cur += piece
        else:
            out.append((cur, cur))
            cur = piece
    if cur:
        out.append((cur, cur))
    return out


def _wrap_tokens(tokens: list[tuple[str, str]], budget: int) -> list[tuple[str, str]]:
    expanded = [piece for p, s in tokens for piece in _split_long_token(p, s, budget)]
    lines: list[tuple[str, str]] = []
    cur_p = ""
    cur_s = ""
    for plain, styled in expanded:
        if not cur_p:
            cur_p, cur_s = plain, styled
        elif len(cur_p) + 1 + len(plain) <= budget:
            cur_p += " " + plain
            cur_s += " " + styled
        else:
            lines.append((cur_p, cur_s))
            cur_p, cur_s = plain, styled
    if cur_p:
        lines.append((cur_p, cur_s))
    return lines or [("", "")]


def _wrap_pays(team: str, cost_centers: list[str], budget: int) -> list[str]:
    if not cost_centers:
        return [team]
    joined = ", ".join(cost_centers)
    single = f"{team} / {joined}"
    if len(single) <= budget:
        return [single]
    if len(f"/ {joined}") <= budget:
        return [team, f"/ {joined}"]
    out = [team]
    cur = "/ "
    for idx, cc in enumerate(cost_centers):
        piece = cc if idx == len(cost_centers) - 1 else f"{cc},"
        sep = "" if cur == "/ " else " "
        if cur == "/ " or len(cur) + len(sep) + len(piece) <= budget:
            cur += sep + piece
        else:
            out.append(cur)
            cur = piece
    if cur.strip():
        out.append(cur)
    return out


def _format_callers_table(callers: list[dict[str, Any]], *, color: bool) -> list[str]:
    if not callers:
        return [_paint("   (no callers)", "2", color=color)]

    indent = "   "
    gap = "  "
    rows_single: list[dict[str, Any]] = []
    for c in callers:
        pays = c.get("pays") or {}
        team = pays.get("team") or "-"
        ccs = list(pays.get("cost_centers") or [])
        pays_text = f"{team} / {', '.join(ccs)}" if ccs else team
        extras = _caller_extras(c, color=color)
        extras_plain = " ".join(p for p, _ in extras)
        extras_styled = " ".join(s for _, s in extras)
        rows_single.append({
            "caller": c["caller"],
            "identity": c["identity"],
            "via": c.get("gateway") or "-",
            "team": team,
            "ccs": ccs,
            "pays_text": pays_text,
            "extras": extras,
            "extras_plain": extras_plain,
            "extras_styled": extras_styled,
        })

    w_caller = max(len(_CALLER_HEADERS[0]), *(len(r["caller"]) for r in rows_single))
    w_ident = max(len(_CALLER_HEADERS[1]), *(len(r["identity"]) for r in rows_single))
    w_via = max(len(_CALLER_HEADERS[2]), *(len(r["via"]) for r in rows_single))
    w_pays = max(len(_CALLER_HEADERS[3]), *(len(r["pays_text"]) for r in rows_single))
    w_extras = max(len(_CALLER_HEADERS[4]), *(len(r["extras_plain"]) for r in rows_single))

    fixed_overhead = len(indent) + 4 * len(gap)
    wrap_mode = (fixed_overhead + w_caller + w_ident + w_via + w_pays + w_extras) > MAX_WIDTH

    rendered_rows: list[tuple[str, str, str, list[str], list[tuple[str, str]]]] = []
    if not wrap_mode:
        for r in rows_single:
            rendered_rows.append(
                (r["caller"], r["identity"], r["via"], [r["pays_text"]], [(r["extras_plain"], r["extras_styled"])])
            )
    else:
        max_team_or_cc = max(
            len(_CALLER_HEADERS[3]),
            *(max(len(r["team"]), len(f"/ {', '.join(r['ccs'])}") if r["ccs"] else 0) for r in rows_single),
        )
        pays_room = max(len(_CALLER_HEADERS[3]), MAX_WIDTH - fixed_overhead - w_caller - w_ident - w_via - 18)
        pays_budget = min(max_team_or_cc, pays_room)
        wrapped_pays = [_wrap_pays(r["team"], r["ccs"], pays_budget) for r in rows_single]
        w_pays = max(len(_CALLER_HEADERS[3]), *(len(line) for lines in wrapped_pays for line in lines))
        extras_budget = max(16, MAX_WIDTH - (fixed_overhead + w_caller + w_ident + w_via + w_pays))
        for r, p_lines in zip(rows_single, wrapped_pays, strict=True):
            e_lines = _wrap_tokens(r["extras"], extras_budget)
            rendered_rows.append((r["caller"], r["identity"], r["via"], p_lines, e_lines))

    header_plain = (
        f"{indent}"
        f"{_pad(_CALLER_HEADERS[0], w_caller)}{gap}"
        f"{_pad(_CALLER_HEADERS[1], w_ident)}{gap}"
        f"{_pad(_CALLER_HEADERS[2], w_via)}{gap}"
        f"{_pad(_CALLER_HEADERS[3], w_pays)}{gap}"
        f"{_CALLER_HEADERS[4]}"
    )
    out = [_paint(header_plain, "2", color=color)]
    for caller, ident, via, p_lines, e_lines in rendered_rows:
        n_sub = max(len(p_lines), len(e_lines))
        for idx in range(n_sub):
            c_col = _pad(caller if idx == 0 else "", w_caller)
            i_col = _pad(ident if idx == 0 else "", w_ident)
            v_col = _pad(via if idx == 0 else "", w_via)
            p_text = p_lines[idx] if idx < len(p_lines) else ""
            e_plain, e_styled = e_lines[idx] if idx < len(e_lines) else ("", "")
            p_col = _pad(p_text, w_pays) if e_plain else p_text
            line = f"{indent}{c_col}{gap}{i_col}{gap}{v_col}{gap}{p_col}"
            if e_plain:
                line += f"{gap}{e_styled}"
            out.append(line.rstrip())
    return out


def _format_target_tree(
    target: dict[str, Any],
    *,
    is_last: bool,
    color: bool,
) -> tuple[list[str], bool]:
    branch = "└─ " if is_last else "├─ "
    cont = "   " if is_last else "│  "
    lines = [f"{branch}{_target_header(target, color=color)}"]
    segments, mismatch = _detail_segments(target, color=color)
    callers = target.get("callers") or []

    detail_lines = _pack_segments(segments, "", color=color, max_width=MAX_WIDTH - len(cont) - 3)
    for idx, dline in enumerate(detail_lines):
        prefix = f"{cont}├─ " if idx == 0 else f"{cont}│  "
        lines.append(f"{prefix}{dline}")

    if target.get("provenance"):
        lines += format_provenance(target["provenance"], indent=f"{cont}│  ", color=color)

    if not callers:
        lines.append(f"{cont}└─ {_paint('(no callers)', '2', color=color)}")
        return lines, mismatch

    for idx, c in enumerate(callers):
        c_last = idx == len(callers) - 1
        c_branch = f"{cont}└─ " if c_last else f"{cont}├─ "
        c_cont = f"{cont}   " if c_last else f"{cont}│  "
        pays = c.get("pays") or {}
        team = pays.get("team") or "-"
        ccs = ", ".join(pays.get("cost_centers") or [])
        pays_text = f"{team} / {ccs}" if ccs else team
        via_text = c.get("gateway") or "-"
        extras = _caller_extras(c, color=color)
        parts: list[tuple[str, str]] = [
            (c["caller"], _paint(c["caller"], "1", color=color)),
            (c["identity"], c["identity"]),
            (f"via {via_text}", f"via {via_text}"),
            (f"pays: {pays_text}", f"pays: {pays_text}"),
            *extras,
        ]
        plain_sep = " · "
        styled_sep = _paint(plain_sep, "2", color=color)
        cur_p = c_branch
        cur_s = c_branch
        first_on_line = True
        for plain, styled in parts:
            joiner_p = "" if first_on_line else plain_sep
            joiner_s = "" if first_on_line else styled_sep
            if first_on_line or len(cur_p) + len(joiner_p) + len(plain) <= MAX_WIDTH:
                cur_p += joiner_p + plain
                cur_s += joiner_s + styled
                first_on_line = False
            else:
                lines.append(cur_s)
                cur_p = c_cont + plain
                cur_s = c_cont + styled
                first_on_line = False
        if cur_p:
            lines.append(cur_s)
    return lines, mismatch


def _format_teams(teams: list[dict[str, Any]], *, color: bool) -> list[str]:
    lines: list[str] = []
    for team in teams:
        projects = f" projects={', '.join(team['projects'])}" if team.get("projects") else ""
        cc = ", ".join(team.get("cost_centers") or []) or "-"
        lines.append(f"👥 {_paint(team['team'], '1', color=color)} (cost centres {cc}){projects}")
        if team.get("provenance"):
            lines += format_provenance(team["provenance"], color=color)
    return lines


def _format_summary(targets: list[dict[str, Any]], mismatches: int, *, color: bool) -> str:
    n_targets = len(targets)
    n_grants = sum(len(t.get("callers") or []) for t in targets)
    n_examples = sum(1 for t in targets if t.get("example"))
    parts = [
        f"{n_targets} target{'s' if n_targets != 1 else ''}",
        f"{n_grants} grant{'s' if n_grants != 1 else ''}",
    ]
    if n_examples:
        parts.append(f"{n_examples} example{'s' if n_examples != 1 else ''}")
    if mismatches:
        mismatch_text = f"⚠️ {mismatches} route mismatch{'es' if mismatches != 1 else ''}"
        parts.append(_paint(mismatch_text, "33;1", color=color))
    sep = _paint(" · ", "2", color=color)
    label = _paint("Summary: ", "1", color=color)
    return label + sep.join(parts)


def _format_legend(*, color: bool) -> list[str]:
    def entry(key: str, text: str) -> str:
        return f"  {_paint(f'{key:<13}', '2', color=color)} {text}"

    cont = " " * 16
    return [
        _paint("Legend:", "1", color=color),
        entry("route / via", "direct = entry point directly to Cloud Run / Agent Runtime; apigee = Apigee gateway;"),
        f"{cont}agent_gateway = outbound Central Agent Gateway (mismatch shows gateway != ingress)",
        entry("identity", "workload (agent) = agent's own Agent Identity; non-agent: service_account, group,"),
        f"{cont}apigee_app, federated (OIDC), spiffe",
        entry("pays", "calling team / cost centers this caller may declare"),
        entry("policy", "unmapped_caller=deny|flag (guard action on unknown caller);"),
        f"{cont}declared_mismatch=reject|bill_own_team (foreign cost center handling)",
        entry(
            "badges",
            f"{_paint('[enforced]', '32', color=color)} compiled into IAM/gateway; "
            f"{_paint('[declared only]', '33', color=color)} not enforced;",
        ),
        f"{cont}{_paint('[example]', '36', color=color)} {_paint('[not compiled]', '33', color=color)} "
        f"skipped from enforcement; {_paint('[adopted from <src>]', '36', color=color)} pinned entry",
    ]


def format_show(
    data: dict[str, Any],
    *,
    fmt: str = "table",
    legend: bool = True,
    color: bool | None = None,
) -> str:
    """Format the output of :func:`esmeralda.catalog.show` for humans."""
    if fmt not in ("table", "tree"):
        raise ValueError(f"unsupported catalog show format: {fmt!r}")
    use_ansi = use_color(color)
    policy = data.get("policy") or {}
    env_label = f"Catalog {data.get('env')!r}"
    header = (
        f"📇 {_paint(env_label, '1', color=use_ansi)}"
        f"{_paint(' · ', '2', color=use_ansi)}"
        f"{_paint('policy:', '2', color=use_ansi)} "
        f"unmapped_caller={policy.get('unmapped_caller', '-')}, "
        f"declared_mismatch={policy.get('declared_mismatch', '-')}"
    )
    lines: list[str] = [header]
    targets = data.get("targets") or []
    mismatches = 0

    if not targets:
        lines.append("")
        lines.append(_paint("   (no matching targets)", "2", color=use_ansi))
    elif fmt == "tree":
        for idx, t in enumerate(targets):
            lines.append("")
            block, mismatch = _format_target_tree(t, is_last=idx == len(targets) - 1, color=use_ansi)
            mismatches += int(mismatch)
            lines += block
    else:
        for t in targets:
            lines.append("")
            lines.append(_target_header(t, color=use_ansi))
            segments, mismatch = _detail_segments(t, color=use_ansi)
            mismatches += int(mismatch)
            lines += _pack_segments(segments, "   ", color=use_ansi)
            if t.get("provenance"):
                lines += format_provenance(t["provenance"], color=use_ansi)
            lines += _format_callers_table(t.get("callers") or [], color=use_ansi)

    if data.get("teams"):
        lines.append("")
        lines += _format_teams(data["teams"], color=use_ansi)

    lines.append("")
    lines.append(_format_summary(targets, mismatches, color=use_ansi))
    if legend:
        lines.append("")
        lines += _format_legend(color=use_ansi)
    return "\n".join(lines)
