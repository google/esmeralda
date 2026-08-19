#!/usr/bin/env python3
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

import argparse
import os
import re
import subprocess
import sys

SERVICES = [
    "a2a-agent",
    "root-agent",
    "income-verification-api",
    "corporate-email",
    "legacy-dms",
    "kong-gateway",
]

def get_repo_root() -> str:
    current = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(current))

def get_prd_env_path() -> str:
    return os.path.join(get_repo_root(), "infrastructure", "live", "prd", "env.yaml")

def get_current_prd_tag() -> str:
    path = get_prd_env_path()
    if not os.path.exists(path):
        return "v1.0.0"
    with open(path, "r") as f:
        content = f.read()
    match = re.search(r'container_tag\s*:\s*"([^"]+)"', content)
    if match:
        return match.group(1)
    return "v1.0.0"

def get_cicd_project_and_region() -> tuple[str, str]:
    path = get_prd_env_path()
    region = "us-central1"
    cicd_proj = "esmeralda-cicd-artifacts-3a3d"
    if os.path.exists(path):
        with open(path, "r") as f:
            content = f.read()
        r_match = re.search(r'region\s*:\s*"([^"]+)"', content)
        if r_match:
            region = r_match.group(1)
        c_match = re.search(r'existing_cicd_project\s*:\s*"([^"]+)"', content)
        if c_match:
            cicd_proj = c_match.group(1)
    return cicd_proj, region

def parse_semver(tag: str) -> tuple[int, int, int]:
    clean = tag.lstrip("v")
    parts = clean.split(".")
    try:
        major = int(parts[0]) if len(parts) > 0 else 1
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
        return major, minor, patch
    except ValueError:
        return 1, 0, 0

def bump_semver(current: str, bump_type: str) -> str:
    major, minor, patch = parse_semver(current)
    if bump_type == "major":
        return f"v{major + 1}.0.0"
    elif bump_type == "minor":
        return f"v{major}.{minor + 1}.0"
    elif bump_type == "patch":
        return f"v{major}.{minor}.{patch + 1}"
    return current

def update_prd_env_tag(new_tag: str):
    path = get_prd_env_path()
    if not os.path.exists(path):
        print(f"❌ Cannot find {path}")
        sys.exit(1)
    with open(path, "r") as f:
        content = f.read()

    if "container_tag:" in content:
        new_content = re.sub(
            r'(container_tag\s*:\s*)"[^"]+"',
            f'\\1"{new_tag}"',
            content
        )
    else:
        # Insert container_tag before closing brace of locals
        new_content = content.replace(
            "  # 📊 OBSERVABILITY & MONITORING CONFIGURATION:",
            f"  container_tag       = \"{new_tag}\"\n\n  # 📊 OBSERVABILITY & MONITORING CONFIGURATION:"
        )

    with open(path, "w") as f:
        f.write(new_content)

def status_command():
    current_tag = get_current_prd_tag()
    major, minor, patch = parse_semver(current_tag)
    next_patch = bump_semver(current_tag, "patch")
    next_minor = bump_semver(current_tag, "minor")
    cicd_proj, region = get_cicd_project_and_region()

    print("========================================================================")
    print("👑 ESMERALDA MULTI-ENVIRONMENT RELEASE & PROMOTION STATUS")
    print("========================================================================")
    print(f"• Active PRD Pinned Tag  : {current_tag} (in infrastructure/live/prd/env.yaml)")
    print(f"• Shared Artifact Registry : {region}-docker.pkg.dev/{cicd_proj}/esmeralda-containers")
    print("------------------------------------------------------------------------")
    print("Semantic Release Options:")
    print(f"  [Patch] make promote-patch   -> Promotes latest dev build to {next_patch}")
    print(f"  [Minor] make promote-minor   -> Promotes latest dev build to {next_minor}")
    print(f"  [Custom] make promote TAG=vX -> Promotes latest dev build to custom vX")
    print("\n⚠️ Note: Promotion only tags Docker images and updates prd/env.yaml.")
    print("   Zero automated deployment is performed. You decide when to apply changes.")
    print("========================================================================")

def promote_command(bump: str, custom_tag: str):
    current_tag = get_current_prd_tag()
    if custom_tag:
        new_tag = custom_tag
    else:
        new_tag = bump_semver(current_tag, bump)

    cicd_proj, region = get_cicd_project_and_region()
    registry_base = f"{region}-docker.pkg.dev/{cicd_proj}/esmeralda-containers"

    print(f"🏷️  Promoting release tag: {current_tag} -> {new_tag} ...")
    for svc in SERVICES:
        src = f"{registry_base}/{svc}:latest"
        dst = f"{registry_base}/{svc}:{new_tag}"
        print(f"  -> Tagging {svc}:latest as {svc}:{new_tag} ...")
        cmd = [
            "gcloud", "artifacts", "docker", "tags", "add",
            src, dst, "--quiet"
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            print(f"     ⚠️  Warning for {svc}: {res.stderr.strip()}")

    print(f"\n📝 Updating PRD configuration (infrastructure/live/prd/env.yaml) to container_tag: \"{new_tag}\" ...")
    update_prd_env_tag(new_tag)

    print("\n========================================================================")
    print(f"✅ RELEASE {new_tag} PREPARED & PINNED IN PRD CONFIGURATION!")
    print("========================================================================")
    print("⚠️  NO AUTOMATED CLOUD DEPLOYMENT WAS PERFORMED.")
    print(f"To deploy release {new_tag} to your live PRD GCP environment when ready, run:")
    print("    make deploy-workloads ENV=prd")
    print("========================================================================")

def main():
    parser = argparse.ArgumentParser(description="Esmeralda Release & Promotion Manager")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    subparsers.add_parser("status", help="Show current release status and promotion options")
    
    prom_p = subparsers.add_parser("promote", help="Tag containers and update prd/env.yaml")
    prom_p.add_argument("--bump", choices=["patch", "minor", "major"], default="patch", help="Semantic bump type")
    prom_p.add_argument("--tag", type=str, help="Custom explicit release tag")

    args = parser.parse_args()
    if args.subcommand == "status":
        status_command()
    elif args.subcommand == "promote":
        promote_command(args.bump, args.tag)

if __name__ == "__main__":
    main()
