#!/usr/bin/env python3
"""
Esmeralda Topological Declarative Deployer (DAG Deployer).
Parses esmeralda.yaml to resolve dependencies and deploy components in the correct order.
"""

import os
import sys
import yaml
import subprocess
import logging

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("dag_deployer")

def load_context():
    """Loads root .env context variables into environment."""
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
    env_file = os.path.join(root_dir, ".env")
    if os.path.exists(env_file):
        logger.info(f"Loading context from {env_file}")
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    os.environ[key] = val

def topological_sort(components):
    """Sorts components based on declared dependencies."""
    adj = {c["name"]: set(c.get("dependencies", [])) for c in components}
    visited = {}
    order = []

    def dfs(node):
        if node in visited:
            if visited[node] == "visiting":
                raise ValueError(f"Circular dependency detected in topological DAG at component '{node}'")
            return
        visited[node] = "visiting"
        for dep in adj.get(node, []):
            if dep in adj:
                dfs(dep)
        visited[node] = "visited"
        order.append(node)

    for node in adj:
        if node not in visited:
            dfs(node)
    return order

def run_preflight():
    """Executes preflight checklist first."""
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
    preflight_script = os.path.join(root_dir, "preflight.sh")
    if os.path.exists(preflight_script):
        logger.info("Executing pre-flight checks...")
        res = subprocess.run(["bash", preflight_script])
        if res.returncode != 0:
            logger.error("Pre-flight checks failed. Aborting deployment.")
            sys.exit(1)
    else:
        logger.warning("No preflight.sh found at root directory. Skipping checks.")

def deploy_component(comp, project_id, region):
    """Deploys an individual component in the DAG and returns a result dict."""
    name = comp["name"]
    path = comp["path"]
    logger.info(f"🚀 Deploying component: {name} ({path})")
    
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
    abs_path = os.path.join(root_dir, path)
    
    agent_yaml_path = os.path.join(abs_path, "agent.yaml")
    compiled_yaml_path = os.path.join(abs_path, "tmp-agent.yaml")
    
    # 1. Compile agent.yaml if it exists to avoid mutating the original file
    if os.path.exists(agent_yaml_path):
        logger.info(f"Compiling configuration for '{name}' with environment overrides...")
        with open(agent_yaml_path, "r") as f:
            content = f.read()
        
        # Expand any environment variables like ${A2A_AGENT_URL} or ${PROJECT_ID}
        compiled_content = os.path.expandvars(content)
        
        with open(compiled_yaml_path, "w") as f:
            f.write(compiled_content)
        logger.info(f"Wrote compiled dynamic config to ignored: {compiled_yaml_path}")
        config_file_name = "tmp-agent.yaml"
    else:
        config_file_name = "agent.yaml"

    try:
        deploy_mode = os.environ.get("DEPLOY_MODE", "python")
        
        if deploy_mode == "terraform":
            logger.info(f"📦 Packaging component '{name}' for Terraform...")
            package_script = os.path.join(os.path.dirname(__file__), "../terraform/package_agent.py")
            dist_dir = os.path.join(abs_path, "dist")
            os.makedirs(dist_dir, exist_ok=True)
            
            package_cmd = [
                "uv", "run", "--active", "--no-sync", "python3", package_script,
                f"--agent-dir={abs_path}",
                f"--output-dir={dist_dir}"
            ]
            res_pack = subprocess.run(package_cmd, capture_output=True, text=True)
            if res_pack.returncode != 0:
                logger.error(f"❌ Packaging of '{name}' failed!")
                print(res_pack.stdout)
                print(res_pack.stderr, file=sys.stderr)
                raise RuntimeError(f"Packaging of '{name}' failed with exit code {res_pack.returncode}")
                
            logger.info(f"🚀 Applying Terraform configuration for '{name}'...")
            tf_dir = os.path.join(os.path.dirname(__file__), "../terraform")
            os.makedirs(os.path.join(tf_dir, "states"), exist_ok=True)
            
            bucket_name = os.environ.get("GCS_OFFLOAD_BUCKET_NAME", f"{project_id}-agent-logs-offload")
            psc_attachment = os.environ.get("PSC_NETWORK_ATTACHMENT", "")
            
            # Run terraform init
            subprocess.run(["terraform", "init", "-reconfigure"], cwd=tf_dir, capture_output=True)
            
            # Run terraform apply
            tf_cmd = [
                "terraform", "apply", "-auto-approve",
                f"-state=states/{name}.tfstate",
                f"-var=project_id={project_id}",
                f"-var=region={region}",
                f"-var=staging_bucket_name={bucket_name}",
                f"-var=agent_name={name}",
                f"-var=service_account=test-vm-sa@{project_id}.iam.gserviceaccount.com",
                f"-var=pickle_object_path=../../{path}/dist/agent.pkl",
                f"-var=requirements_path=../../{path}/dist/requirements.txt",
                f"-var=dependencies_path=../../{path}/dist/dependencies.tar.gz",
                f"-var=network_attachment={psc_attachment}"
            ]
            
            logger.info(f"Running Terraform apply for {name}: {' '.join(tf_cmd)}")
            res = subprocess.run(tf_cmd, cwd=tf_dir, capture_output=True, text=True)
        else:
            # Build standard python deployment command
            deploy_script = os.path.join(os.path.dirname(__file__), "deploy_agent.py")
            deploy_cmd = [
                "uv", "run", "--active", "--no-sync", deploy_script,
                f"--project_id={project_id}",
                f"--location={region}",
                f"--config-file={config_file_name}",
                "--source-packages=.",
                "--requirements-file=requirements.txt"
            ]
            
            # Support PSC attachment if present in environment
            psc_attachment = os.environ.get("PSC_NETWORK_ATTACHMENT")
            if psc_attachment:
                deploy_cmd.extend([
                    f"--network-attachment={psc_attachment}",
                    "--dns-peering-domain=gateway",
                    "--dns-peering-target-network=gateway-vpc"
                ])
                
            deploy_cmd.append(f"--service-account=test-vm-sa@{project_id}.iam.gserviceaccount.com")
            
            # Execute deployment
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{abs_path}:{env.get('PYTHONPATH', '')}"
            
            # Explicitly activate the agent's virtual environment if it exists
            venv_path = os.path.join(abs_path, ".venv")
            if os.path.exists(venv_path):
                env["VIRTUAL_ENV"] = venv_path
                env["PATH"] = f"{os.path.join(venv_path, 'bin')}:{env.get('PATH', '')}"
            
            logger.info(f"Running deployment command for {name}: {' '.join(deploy_cmd)}")
            res = subprocess.run(deploy_cmd, cwd=abs_path, env=env, capture_output=True, text=True)
        
        if res.returncode != 0:
            logger.error(f"❌ Deployment of component '{name}' failed!")
            print(res.stdout)
            print(res.stderr, file=sys.stderr)
            raise RuntimeError(f"Deployment of '{name}' failed with exit code {res.returncode}")
            
        logger.info(f"✅ Component '{name}' deployed successfully.")
        
        # Provide the end of the deployment log to the user
        stdout_lines = res.stdout.splitlines()
        num_lines = min(25, len(stdout_lines))
        print(f"\n==================================================")
        print(f"📖 END OF DEPLOYMENT LOG FOR '{name}' (last {num_lines} lines):")
        print(f"==================================================")
        for line in stdout_lines[-num_lines:]:
            print(line)
        print(f"==================================================\n")
        
        # Capture Base ADK Agent Engine ID to update .env
        if name == "base-adk-agent":
            import re
            match = re.search(r"projects/[^'\"]*reasoningEngines/[0-9]*", res.stdout)
            if match:
                engine_id = match.group(0)
                env_file = os.path.join(root_dir, ".env")
                if os.path.exists(env_file):
                    # Update root .env
                    with open(env_file, "r") as f:
                        lines = f.readlines()
                    # Exclude REMOTE_AGENT_ENGINE_ID
                    lines = [l for l in lines if not l.strip().startswith("REMOTE_AGENT_ENGINE_ID=")]
                    lines.append(f'\nREMOTE_AGENT_ENGINE_ID="{engine_id}"\n')
                    with open(env_file, "w") as f:
                        f.writelines(lines)
                    logger.info(f"Updated REMOTE_AGENT_ENGINE_ID in root .env context: {engine_id}")

        return {
            "name": name,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "returncode": res.returncode
        }
    finally:
        # Clean up temporary compiled file safely
        if os.path.exists(compiled_yaml_path):
            os.remove(compiled_yaml_path)
            logger.info(f"Cleaned up temporary compiled configuration: {compiled_yaml_path}")

def deploy_with_dependencies(comp, project_id, region, futures_dict):
    """Waits for dependencies to finish, applies runtime overrides in-memory, then triggers deploy."""
    name = comp["name"]
    
    # 1. Wait for all dependencies to finish successfully
    for dep in comp.get("dependencies", []):
        if dep in futures_dict:
            try:
                # result() will block until the dependency completes or raises an exception
                futures_dict[dep].result()
            except Exception as e:
                logger.error(f"⚠️ Dependency '{dep}' failed. Skipping deployment of '{name}'.")
                raise RuntimeError(f"Dependency '{dep}' failed.") from e

    # 2. Extract and resolve dependencies context dynamically
    if name == "base-adk-agent":
        a2a_future = futures_dict.get("a2a-agent")
        if a2a_future:
            try:
                a2a_res = a2a_future.result()
                a2a_out = a2a_res.get("stdout", "")
                import re
                match = re.search(r"projects/[^'\"]*reasoningEngines/[0-9]*", a2a_out)
                if match:
                    engine_resource = match.group(0)
                    a2a_url = f"https://{region}-aiplatform.googleapis.com/v1beta1/{engine_resource}/a2a"
                    logger.info(f"Dynamic Resolve: Extracted A2A URL from deployment logs: {a2a_url}")
                    # Set as environment variable so it gets expanded in base-adk-agent's template compile
                    os.environ["A2A_AGENT_URL"] = a2a_url
            except Exception as e:
                logger.warning(f"Could not auto-resolve dynamic context parameters: {e}")

    # 3. Trigger actual component deployment
    return deploy_component(comp, project_id, region)

def main():
    load_context()
    
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
    manifest_path = os.path.join(root_dir, "esmeralda.yaml")
    
    if not os.path.exists(manifest_path):
        logger.error(f"Manifest file '{manifest_path}' not found.")
        sys.exit(1)
        
    logger.info(f"Reading topology manifest from {manifest_path}...")
    with open(manifest_path) as f:
        manifest = yaml.safe_load(f)
        
    project_id = os.environ.get("PROJECT_ID")
    if not project_id:
        logger.error("PROJECT_ID environment variable or context not set.")
        sys.exit(1)
        
    region = manifest.get("project", {}).get("region", "us-central1")
    components = manifest.get("components", [])
    
    logger.info("Computing Directed Acyclic Graph (DAG) for components...")
    sorted_order = topological_sort(components)
    logger.info(f"Resolved deployment order: {' -> '.join(sorted_order)}")
    
    run_preflight()
    
    # Map component name to its dictionary
    comp_map = {c["name"]: c for c in components}
    
    import concurrent.futures
    futures_dict = {}
    
    max_workers = min(len(components), 8)
    logger.info(f"Initiating parallelized asynchronous deployments with max_workers={max_workers}...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit tasks in topological order so dependencies are registered first
        for comp_name in sorted_order:
            comp = comp_map[comp_name]
            future = executor.submit(
                deploy_with_dependencies,
                comp,
                project_id,
                region,
                futures_dict
            )
            futures_dict[comp_name] = future
            
        # Monitor results
        failed = False
        for comp_name in sorted_order:
            try:
                futures_dict[comp_name].result()
            except Exception as e:
                logger.error(f"❌ Component '{comp_name}' failed to deploy: {e}")
                failed = True
                
        if failed:
            logger.error("Some components failed to deploy. Please check the logs.")
            sys.exit(1)
            
    logger.info("🎉 All declarative components deployed successfully in parallel according to the DAG topology!")

if __name__ == "__main__":
    main()
