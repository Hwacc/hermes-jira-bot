#!/usr/bin/env python3
"""Resolve Jira project.key + fixVersion (+ optional summary override) → git/PR target.

See config/repos.template.json and vault note「Jira Bot 项目状态」for rules.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional


_NUMERIC_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


@dataclass
class VersionNorm:
    """Jira display name → extracted digits → padded x.y.z for matching."""

    raw: Optional[str]
    extracted: Optional[str]  # e.g. 12.0 (as found in the string)
    canonical: Optional[str]  # e.g. 12.0.0 (padded; used for versions / version_lines)


@dataclass
class ResolveResult:
    project_key: str
    product_id: str
    provider: str
    workspace: str
    repo: str
    path: str
    branch: str
    agent: str
    version: Optional[str]  # canonical padded x.y.z when extractable
    version_raw: Optional[str] = None
    version_extracted: Optional[str] = None
    lint: Optional[str] = None
    test: Optional[str] = None
    matched_via: str = ""


def strip_leading_v(version: str) -> str:
    v = (version or "").strip()
    if len(v) >= 2 and v[0] in "vV" and v[1].isdigit():
        return v[1:]
    return v


def extract_numeric_version(raw: Optional[str]) -> Optional[str]:
    """Pull the first x.y or x.y.z from a marketing / irregular version string."""
    if not raw or not str(raw).strip():
        return None
    m = _NUMERIC_VERSION_RE.search(str(raw).strip())
    return m.group(1) if m else None


def pad_semver(version: str) -> str:
    """Pad to x.y.z (e.g. 12.0 → 12.0.0). Truncates to 3 components."""
    parts = [p for p in str(version).strip().split(".") if p != ""]
    if not parts:
        return str(version).strip()
    while len(parts) < 3:
        parts.append("0")
    return ".".join(parts[:3])


def normalize_version(raw: Optional[str]) -> VersionNorm:
    if raw is None or not str(raw).strip():
        return VersionNorm(None, None, None)
    text = str(raw).strip()
    extracted = extract_numeric_version(text)
    if not extracted:
        return VersionNorm(text, None, None)
    return VersionNorm(text, extracted, pad_semver(extracted))


def _match_key(version_name: str) -> str:
    """Canonical key for equality: padded semver if numeric, else strip-v lower."""
    ext = extract_numeric_version(version_name)
    if ext:
        return pad_semver(ext)
    return strip_leading_v(version_name).lower()


def find_versions_entry(versions: dict, version_name: str) -> Optional[dict]:
    if not version_name or not versions:
        return None
    target = _match_key(version_name)
    for k, v in versions.items():
        if _match_key(str(k)) == target:
            return v
    return None


def find_version_line(version_lines: dict, version_name: str) -> Optional[dict]:
    if not version_name or not version_lines:
        return None
    candidates: list[str] = []
    ext = extract_numeric_version(version_name)
    if ext:
        candidates.append(pad_semver(ext).lower())
        candidates.append(ext.lower())
    candidates.append(strip_leading_v(version_name).lower())

    best_cfg = None
    best_len = -1
    for cand in candidates:
        for prefix, cfg in version_lines.items():
            pl = str(prefix).lower()
            if cand.startswith(pl) and len(pl) > best_len:
                best_cfg = cfg
                best_len = len(pl)
    return best_cfg


def version_for_branch_pattern(version_for_branch: str) -> str:
    """Form used in branch_pattern {version}: always major.minor (ignore patch).

    11.11.0 / 11.11.2 / 11.11 → 11.11 → dev/11.11.x
    11.12 → 11.12 → dev/11.12.x
    """
    ver = version_for_branch.strip()
    ext = extract_numeric_version(ver)
    if not ext:
        return strip_leading_v(ver)
    parts = ext.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return ext


def apply_branch_pattern(pattern: str, version_for_branch: str) -> str:
    """Substitute {version}. Matching may use padded x.y.z; branches always major.minor."""
    return pattern.replace("{version}", version_for_branch_pattern(version_for_branch))


def _merge(base: dict, overlay: dict) -> dict:
    out = dict(base or {})
    for k, v in (overlay or {}).items():
        if v is not None:
            out[k] = v
    return out


def _pick_override(project: dict, summary: str, force_product: Optional[str]) -> Optional[dict]:
    overrides = project.get("overrides") or []
    if force_product:
        for o in overrides:
            if o.get("id") == force_product:
                return o
        raise ValueError(f"unknown product override id: {force_product}")
    text = summary or ""
    for o in overrides:
        pat = (o.get("match") or {}).get("summary")
        if not pat:
            continue
        if re.search(pat, text):
            return o
    return None


def _resolve_branch_and_repo(
    scope: dict,
    project: dict,
    version_name: Optional[str],
    locked: Optional[dict] = None,
) -> tuple[dict, str]:
    """Return (repo fields dict, matched_via).

    locked: if set (from override), repo/path come from locked; branch still resolved.
    scope: override or project dict that may contain versions/version_lines/branch_pattern/default.

    Matching uses padded canonical (12.0.0). branch_pattern uses extracted (12.0).
    """
    project_defaults = {
        "provider": project.get("provider", "bitbucket"),
        "workspace": project.get("workspace"),
        "agent": project.get("agent", "claude"),
        "lint": project.get("lint"),
        "test": project.get("test"),
    }
    default = scope.get("default") or project.get("default") or {}
    branch_pattern = (
        scope.get("branch_pattern")
        or project.get("branch_pattern")
        or "dev/{version}.x"
    )

    base = _merge(project_defaults, default)
    if locked:
        base = _merge(base, {k: locked[k] for k in ("repo", "path", "workspace", "provider", "agent", "lint", "test") if k in locked and locked[k] is not None})

    versions = scope.get("versions") or {}
    version_lines = scope.get("version_lines") or {}

    if not version_name:
        return base, "default(no-version)"

    norm = normalize_version(version_name)
    # Equality / prefix match on padded form when possible; else raw strip-v
    match_ver = norm.canonical or strip_leading_v(version_name)
    # Keep branch names like dev/12.0.x (not dev/12.0.0.x)
    branch_ver = norm.extracted or match_ver

    entry = find_versions_entry(versions, match_ver)
    if entry:
        merged = _merge(base, entry)
        if locked:
            for k in ("repo", "path"):
                if k in locked and locked[k]:
                    merged[k] = locked[k]
        return merged, "versions"

    # Prefer scope version_lines; if locked override has none, do NOT use project
    # version_lines for repo/path (those point at Cortex PC repos). Only use
    # branch_pattern for branch when locked.
    if locked:
        line = find_version_line(version_lines, match_ver) if version_lines else None
        if line:
            merged = _merge(base, line)
            for k in ("repo", "path"):
                if k in locked and locked[k]:
                    merged[k] = locked[k]
            if "branch" not in line:
                merged["branch"] = apply_branch_pattern(branch_pattern, branch_ver)
            return merged, "override.version_lines+pattern"
        merged = dict(base)
        merged["branch"] = apply_branch_pattern(branch_pattern, branch_ver)
        return merged, "override+branch_pattern"

    line = find_version_line(version_lines or project.get("version_lines") or {}, match_ver)
    if line:
        merged = _merge(base, line)
        if "branch" not in line:
            merged["branch"] = apply_branch_pattern(branch_pattern, branch_ver)
        return merged, "version_lines+pattern"

    merged = dict(base)
    merged["branch"] = apply_branch_pattern(branch_pattern, branch_ver)
    return merged, "default+branch_pattern"


def resolve(
    repos_config: dict,
    project_key: str,
    *,
    summary: str = "",
    version_name: Optional[str] = None,
    force_product: Optional[str] = None,
) -> ResolveResult:
    project = repos_config.get(project_key)
    if not project:
        raise ValueError(f"project key {project_key!r} not found in repos.json")

    norm = normalize_version(version_name)

    override = _pick_override(project, summary, force_product)
    if override:
        fields, via = _resolve_branch_and_repo(
            override, project, version_name, locked=override
        )
        product_id = override.get("id") or "override"
        matched_via = f"override:{product_id}/{via}"
    else:
        fields, via = _resolve_branch_and_repo(project, project, version_name)
        product_id = "default"
        matched_via = via

    required = ("provider", "workspace", "repo", "path", "branch")
    missing = [k for k in required if not fields.get(k)]
    if missing:
        raise ValueError(f"incomplete resolve for {project_key}: missing {missing} ({matched_via})")

    return ResolveResult(
        project_key=project_key,
        product_id=product_id,
        provider=fields["provider"],
        workspace=fields["workspace"],
        repo=fields["repo"],
        path=fields["path"],
        branch=fields["branch"],
        agent=fields.get("agent") or "claude",
        version=norm.canonical or version_name,
        version_raw=norm.raw,
        version_extracted=norm.extracted,
        lint=fields.get("lint"),
        test=fields.get("test"),
        matched_via=matched_via,
    )


def load_repos(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def project_key_from_issue(issue_key: str) -> str:
    return issue_key.split("-", 1)[0]


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Resolve repos.json mapping")
    ap.add_argument("issue_key", help="e.g. CG-20914")
    ap.add_argument("--summary", default="")
    ap.add_argument("--version", default=None)
    ap.add_argument("--product", default=None, help="force override id, e.g. overlay")
    ap.add_argument(
        "--repos",
        default=None,
        help="path to repos.json (default: repo config/repos.json)",
    )
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[3]
    repos_path = Path(args.repos) if args.repos else root / "config" / "repos.json"
    if not repos_path.is_file():
        print(json.dumps({"error": f"repos.json not found: {repos_path}"}), file=sys.stderr)
        sys.exit(1)

    cfg = load_repos(repos_path)
    pk = project_key_from_issue(args.issue_key)
    try:
        r = resolve(
            cfg,
            pk,
            summary=args.summary,
            version_name=args.version,
            force_product=args.product,
        )
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(asdict(r), ensure_ascii=False, indent=2))
