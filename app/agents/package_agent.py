#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import sys
import importlib
import argparse
import shutil
import tarfile

def create_dependencies_tar(agent_dir, output_path):
    print(f"[*] Packaging dependency files into {output_path}...")
    with tarfile.open(output_path, "w:gz") as tar:
        for root, dirs, files in os.walk(agent_dir):
            # Prune unwanted directories
            dirs[:] = [d for d in dirs if d not in (".venv", "dist", ".git", "__pycache__", "tests")]
            for file in files:
                if file.endswith((".pyc", ".pyo", ".gitkeep")):
                    continue
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, agent_dir)
                tar.add(file_path, arcname=arcname)

def main():
    parser = argparse.ArgumentParser(description="Package ADK agent into serialized pickle and requirements.")
    parser.add_argument("--agent-dir", required=True, help="Path to the agent directory")
    parser.add_argument("--output-dir", required=True, help="Path to save packaged files")
    args = parser.parse_args()

    agent_dir = os.path.abspath(args.agent_dir)
    output_dir = os.path.abspath(args.output_dir)

    os.makedirs(output_dir, exist_ok=True)

    # Add agent_dir to Python path so relative imports work
    sys.path.insert(0, agent_dir)

    # Read agent.yaml to find the entrypoint
    yaml_path = os.path.join(agent_dir, "agent.yaml")
    if not os.path.exists(yaml_path):
        print(f"❌ Error: agent.yaml not found at {yaml_path}")
        sys.exit(1)

    # Import yaml dynamically
    try:
        import yaml
    except ImportError:
        print("[*] yaml not found, installing PyYAML via pip...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "PyYAML"])
        import yaml

    with open(yaml_path, "r") as f:
        agent_config = yaml.safe_load(f)

    entrypoint = agent_config.get("entrypoint", {})
    module_name = entrypoint.get("module", "agent_app")
    object_name = entrypoint.get("object", "adk_app")

    print(f"[*] Loading object '{object_name}' from module '{module_name}' in {agent_dir}...")
    
    # Pre-set basic environment variables so modules don't crash on import
    os.environ["GOOGLE_CLOUD_LOCATION"] = os.environ.get("REGION", "us-central1")
    os.environ["GOOGLE_CLOUD_PROJECT"] = os.environ.get("PROJECT_ID", "dummy-project")
    os.environ["USE_CUSTOM_TELEMETRY"] = "False"
    
    try:
        module = importlib.import_module(module_name)
        app_obj = getattr(module, object_name)
    except Exception as e:
        print(f"❌ Error importing agent app: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Import cloudpickle dynamically
    try:
        import cloudpickle
    except ImportError:
        print("[*] cloudpickle not found, installing via pip...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "cloudpickle"])
        import cloudpickle

    # Serialize agent app using cloudpickle
    pkl_path = os.path.join(output_dir, "agent.pkl")
    print(f"[*] Serializing agent to {pkl_path}...")
    try:
        with open(pkl_path, "wb") as f:
            cloudpickle.dump(app_obj, f)
    except Exception as e:
        print(f"❌ Error during serialization: {e}")
        sys.exit(1)

    # Copy requirements.txt to output_dir, or extract it dynamically from pyproject.toml
    req_src = os.path.join(agent_dir, "requirements.txt")
    req_dst = os.path.join(output_dir, "requirements.txt")
    pyproject_src = os.path.join(agent_dir, "pyproject.toml")
    if os.path.exists(req_src):
        print(f"[*] Copying requirements.txt to {req_dst}...")
        shutil.copy2(req_src, req_dst)
    elif os.path.exists(pyproject_src):
        print(f"[*] Extracting requirements dynamically from {pyproject_src} to {req_dst}...")
        try:
            import tomllib
            with open(pyproject_src, "rb") as f_toml:
                pyproject_data = tomllib.load(f_toml)
        except ImportError:
            try:
                import toml
                with open(pyproject_src, "r") as f_toml:
                    pyproject_data = toml.load(f_toml)
            except ImportError:
                # Custom lightweight parser for pyproject.toml dependencies if toml/tomllib not available
                pyproject_data = {}
                deps = []
                in_deps = False
                with open(pyproject_src, "r") as f_toml:
                    for line in f_toml:
                        line = line.strip()
                        if line.startswith("dependencies = ["):
                            in_deps = True
                            continue
                        if in_deps:
                            if line.startswith("]"):
                                in_deps = False
                                break
                            dep = line.strip('", \t\'')
                            if dep:
                                deps.append(dep)
                pyproject_data["project"] = {"dependencies": deps}

        deps = pyproject_data.get("project", {}).get("dependencies", [])
        with open(req_dst, "w") as f_req:
            for dep in deps:
                f_req.write(f"{dep}\n")
    else:
        # Create empty requirements file if not present
        with open(req_dst, "w") as f:
            pass

    # Package dependencies into dependencies.tar.gz
    dependencies_tar_path = os.path.join(output_dir, "dependencies.tar.gz")
    create_dependencies_tar(agent_dir, dependencies_tar_path)

    print("✅ Agent successfully packaged!")

if __name__ == "__main__":
    main()
