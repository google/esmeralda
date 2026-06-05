# Copyright 2025 Google LLC
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
import json
import logging
import argparse
import ast
import yaml
from typing import Any

import google.auth
import vertexai
from vertexai import agent_engines
from vertexai._genai import _agent_engines_utils
from vertexai._genai.types import AgentEngineConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

def parse_env_vars(env_vars_string: str | None) -> dict[str, str]:
    """Parse environment variables from a comma-separated KEY=VALUE string."""
    env_vars = {}
    if env_vars_string:
        for pair in env_vars_string.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                env_vars[key.strip()] = value.strip()
            else:
                logging.warning(f"Skipping malformed environment variable pair: {pair}")
    return env_vars

def generate_class_methods_from_agent(agent_instance: Any) -> list[dict[str, Any]]:
    """Generate method specifications with schemas from agent's register_operations()."""
    registered_operations = _agent_engines_utils._get_registered_operations(
        agent=agent_instance
    )
    class_methods_spec = _agent_engines_utils._generate_class_methods_spec_or_raise(
        agent=agent_instance,
        operations=registered_operations,
    )
    class_methods_list = [
        _agent_engines_utils._to_dict(method_spec) for method_spec in class_methods_spec
    ]
    return class_methods_list


def parse_dict_arg(arg_value: str | None) -> dict | None:
    """Parse a dictionary argument from a string representation."""
    if not arg_value:
        return None
    try:
        # Try to parse as JSON first
        return json.loads(arg_value)
    except json.JSONDecodeError:
        try:
            # Fallback to ast.literal_eval for Python-style dict strings (e.g., single quotes)
            val = ast.literal_eval(arg_value)
            if isinstance(val, dict):
                return val
            else:
                logging.warning(f"Argument value is not a dictionary: {arg_value}")
                return None
        except (ValueError, SyntaxError):
            logging.warning(f"Failed to parse dictionary argument: {arg_value}")
            return None

import time
import urllib.request
import urllib.error

def _ge_deploy(
    project: str,
    app_id: str,
    agent_name: str,
    display_name: str,
    description: str,
    reasoning_engine_name: str,
    oauth_client_id: str,
    oauth_client_secret: str,
) -> None:
    """Register an Agent Engine agent in Gemini Enterprise."""
    import google.auth
    import google.auth.transport.requests

    credentials, _ = google.auth.default()
    credentials.refresh(google.auth.transport.requests.Request())
    access_token = credentials.token

    base_url = f"https://global-discoveryengine.googleapis.com/v1alpha/projects/{project}/locations/global"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": project,
    }

    # Step 1: Delete existing agents by display name
    agents_url = f"{base_url}/collections/default_collection/engines/{app_id}/assistants/default_assistant/agents"
    logger.info(f"Checking for existing agent in Gemini Enterprise engine '{app_id}'...")
    list_req = urllib.request.Request(agents_url, headers=headers)
    try:
        with urllib.request.urlopen(list_req) as resp:
            list_resp = json.loads(resp.read())
            for agent in list_resp.get("agents", []):
                if agent.get("displayName") == display_name:
                    existing_name = agent["name"]
                    logger.info(f"  Deleting existing agent: {existing_name}...")
                    del_agent_url = f"https://global-discoveryengine.googleapis.com/v1alpha/{existing_name}"
                    del_agent_req = urllib.request.Request(del_agent_url, headers=headers, method="DELETE")
                    with urllib.request.urlopen(del_agent_req) as del_resp:
                        del_resp.read()
    except urllib.error.HTTPError as e:
        logger.warning(f"Could not list/delete agents: {e.code}")

    # Step 2: Delete existing authorizations by prefix
    auth_prefix = f"projects/{project}/locations/global/authorizations/{agent_name}"
    list_auth_url = f"{base_url}/authorizations"
    list_auth_req = urllib.request.Request(list_auth_url, headers=headers)
    try:
        with urllib.request.urlopen(list_auth_req) as resp:
            auth_list = json.loads(resp.read())
            for auth in auth_list.get("authorizations", []):
                auth_name = auth.get("name", "")
                if auth_name == auth_prefix or auth_name.startswith(f"{auth_prefix}_"):
                    del_auth_url = f"https://global-discoveryengine.googleapis.com/v1alpha/{auth_name}"
                    del_auth_req = urllib.request.Request(del_auth_url, headers=headers, method="DELETE")
                    try:
                        with urllib.request.urlopen(del_auth_req) as del_resp:
                            del_resp.read()
                    except urllib.error.HTTPError:
                        pass
    except urllib.error.HTTPError:
        pass

    # Step 3: Create authorization with timestamp-suffixed ID.
    auth_id = f"{agent_name}_{int(time.time() * 1000)}"
    auth_resource_name = f"projects/{project}/locations/global/authorizations/{auth_id}"
    auth_url = f"{base_url}/authorizations?authorizationId={auth_id}"
    authorization_uri = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={oauth_client_id}"
        "&redirect_uri=https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fstatic%2Foauth%2Foauth.html"
        "&scope=https://www.googleapis.com/auth/cloud-platform"
        "&include_granted_scopes=true"
        "&response_type=code"
        "&access_type=offline"
        "&prompt=consent"
    )
    auth_body = json.dumps(
        {
            "displayName": auth_id,
            "serverSideOauth2": {
                "clientId": oauth_client_id,
                "clientSecret": oauth_client_secret,
                "tokenUri": "https://oauth2.googleapis.com/token",
                "authorizationUri": authorization_uri,
            },
        }
    ).encode()

    auth_req = urllib.request.Request(auth_url, data=auth_body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(auth_req) as resp:
            auth_resp = json.loads(resp.read())
            auth_resource_name = auth_resp.get("name", auth_resource_name)
    except urllib.error.HTTPError as e:
        logger.error(f"ERROR creating authorization: {e.code}")
        return

    # Step 4: Create agent registration
    agent_body = json.dumps(
        {
            "displayName": display_name,
            "description": description,
            "adk_agent_definition": {
                "provisioned_reasoning_engine": {
                    "reasoning_engine": reasoning_engine_name,
                },
            },
            "authorization_config": {
                "tool_authorizations": [
                    auth_resource_name,
                ],
            },
            "sharingConfig": {
                "scope": "ALL_USERS",
            },
            "agentInvocationSpec": {
                "invocationMode": "AUTOMATIC",
            },
        }
    ).encode()

    agent_req = urllib.request.Request(agents_url, data=agent_body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(agent_req) as resp:
            agent_resp = json.loads(resp.read())
            logger.info(f"Agent successfully registered in Gemini Enterprise: {agent_resp.get('name')}")
    except urllib.error.HTTPError as e:
        logger.error(f"ERROR registering agent in Gemini Enterprise: {e.code}")

def deploy_agent_engine_app(
    project_id: str | None,
    location: str,
    agent_name: str,
    requirements_file: str,
    entrypoint_module: str,
    entrypoint_object: str,
    source_packages: list[str],
    class_methods_file: str | None,
    set_env_vars: dict[str, str] | None,
    service_account: str | None,
    description: str | None,
    labels: str | None,
    build_options: str | None,
    min_instances: str | None,
    max_instances: str | None,
    resource_limits: str | None,
    container_concurrency: str | None,
    agent_framework: str | None,
) -> Any:
    """Deploys or updates a Vertex AI Agent Engine application from source."""
    env_vars = set_env_vars or {}
    labels_dict = parse_dict_arg(labels)
    resource_limits_dict = parse_dict_arg(resource_limits)
    build_options_dict = parse_dict_arg(build_options)

    if not project_id:
        _, project_id = google.auth.default()

    logging.basicConfig(level=logging.INFO)

    # Initialize vertexai client
    client = vertexai.Client(
        project=project_id,
        location=location,
    )
    vertexai.init(project=project_id, location=location)

    # Extract class_methods dynamically from the application object
    try:
        import importlib

        # Inject parsed env vars into local os.environ so the module can initialize
        if set_env_vars:
            for k, v in set_env_vars.items():
                os.environ[str(k)] = str(v)

        sys.path.insert(0, os.path.abspath(source_packages[0]))
        module = importlib.import_module(entrypoint_module)
        app_obj = getattr(module, entrypoint_object)

        try:
            class_methods = generate_class_methods_from_agent(app_obj)
            logger.info(f"Successfully extracted {len(class_methods)} class methods from {entrypoint_module}.{entrypoint_object}")
        except Exception as e_extract:
            logger.warning(f"Spec generation failed: {e_extract}. Building class methods from register_operations().")
            class_methods = []
            if hasattr(app_obj, "register_operations"):
                for api_mode, methods in app_obj.register_operations().items():
                    for method in (methods if isinstance(methods, list) else [methods]):
                        method_name = method.__name__ if hasattr(method, "__name__") else str(method)
                        class_methods.append({
                            "name": method_name,
                            "description": f"Handles {api_mode} {method_name} requests.",
                            "parameters": {"type": "object", "properties": {}},
                            "api_mode": api_mode,
                        })
            if class_methods:
                logger.info(f"Built {len(class_methods)} class methods from register_operations()")

    except Exception as e:
        logger.error(f"Failed to extract class_methods dynamically: {e}")
        raise

    import tempfile
    import shutil
    
    # --- Clean Staging Directory (Filters out .venv) ---
    staging_dir = tempfile.mkdtemp(prefix="agent_deploy_")
    original_cwd = os.getcwd()

    try:
        # Copy all source packages to staging, ignoring heavy/local folders
        for sp in source_packages:
            if sp == ".":
                for item in os.listdir("."):
                    if item not in [".venv", ".git", "__pycache__", ".env", staging_dir]:
                        s = os.path.join(".", item)
                        d = os.path.join(staging_dir, item)
                        if os.path.isdir(s):
                            shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".venv", ".env"))
                        else:
                            shutil.copy2(s, d)
            else:
                shutil.copytree(
                    sp,
                    os.path.join(staging_dir, os.path.basename(sp)),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".venv", ".env")
                )
        
        os.chdir(staging_dir)
        
        # Override source packages to point to the current staging directory
        source_packages = ["."]

        config = {
            "display_name": agent_name,
            "description": description,
            "source_packages": source_packages,
            "entrypoint_module": entrypoint_module,
            "entrypoint_object": entrypoint_object,
            "class_methods": class_methods,
            "requirements_file": requirements_file,
            "env_vars": env_vars,
            "service_account": service_account,
            "labels": labels_dict,
            "build_options": build_options_dict,
            "min_instances": int(min_instances) if min_instances is not None else None,
            "max_instances": int(max_instances) if max_instances is not None else None,
            "resource_limits": resource_limits_dict,
            "container_concurrency": int(container_concurrency) if container_concurrency is not None else None,
            "agent_framework": agent_framework,
        }

        # Support for PSC Interface
        network_attachment = getattr(sys.modules["__main__"].args, "network_attachment", None) if hasattr(sys.modules["__main__"], "args") else None
        if network_attachment:
            psc_config = {"network_attachment": network_attachment}
            dns_peering_domain = getattr(sys.modules["__main__"].args, "dns_peering_domain", None)
            if dns_peering_domain:
                psc_config["dns_peering_configs"] = [{
                    "domain": dns_peering_domain,
                    "target_project": getattr(sys.modules["__main__"].args, "dns_peering_target_project", None) or project_id,
                    "target_network": getattr(sys.modules["__main__"].args, "dns_peering_target_network", None),
                }]
            config["psc_interface_config"] = psc_config

        # Support for Agent Identity
        enable_agent_identity = getattr(sys.modules["__main__"].args, "enable_agent_identity", False) if hasattr(sys.modules["__main__"], "args") else False
        if enable_agent_identity:
            config["identity_type"] = "AGENT_IDENTITY"
        else:
            config["identity_type"] = "IDENTITY_TYPE_UNSPECIFIED"

        print("Deployment Config:", json.dumps(config, default=str, indent=2))

        # Remove empty values from config
        config = {k: v for k, v in config.items() if v}

        # Workaround: agent_framework="a2a" is valid at deploy time but fails
        # Pydantic validation (not in the Literal type). Pop before validation,
        # set on the validated object after.
        actual_agent_framework = config.pop("agent_framework", None)
        config_obj = AgentEngineConfig.model_validate(config)
        config_obj.agent_framework = actual_agent_framework

        # Check if an agent with this name already exists
        existing_agents = list(client.agent_engines.list())
        matching_agents = [
            agent
            for agent in existing_agents
            if agent.api_resource.display_name == agent_name
        ]

        if matching_agents:
            logging.info(f"\n📝 Updating existing agent: {agent_name}")
            try:
                remote_agent = client.agent_engines.update(
                    name=matching_agents[0].api_resource.name,
                    config=config_obj
                )
            except Exception as e:
                logging.warning(f"Failed to update Agent Engine: {e}. Retrying by deleting and recreating...")
                try:
                    # The delete method might be synchronous or asynchronous depending on the SDK version,
                    # but typically it deletes the resource or initiates delete.
                    client.agent_engines.delete(name=matching_agents[0].api_resource.name)
                    logging.info(f"Existing agent {agent_name} deleted successfully.")
                except Exception as del_err:
                    logging.warning(f"Failed to delete existing agent {agent_name}: {del_err}")
                logging.info(f"\n🚀 Creating new agent: {agent_name}")
                remote_agent = client.agent_engines.create(
                    config=config_obj
                )
        else:
            logging.info(f"\n🚀 Creating new agent: {agent_name}")
            remote_agent = client.agent_engines.create(
                config=config_obj
            )

        logging.info(f"✅ Deployment successful! Agent Engine ID: {remote_agent.api_resource.name}")

        if enable_agent_identity and getattr(remote_agent.api_resource.spec, "effective_identity", None):
            principal = f"principal://{remote_agent.api_resource.spec.effective_identity}"
            logging.info(f"🔐 Granting IAM roles to: {principal}")
            roles = [
                "roles/aiplatform.user",
                "roles/serviceusage.serviceUsageConsumer",
                "roles/browser",
                "roles/cloudapiregistry.viewer",
                "roles/logging.logWriter",
                "roles/monitoring.metricWriter",
                "roles/cloudtrace.agent",
                "roles/telemetry.writer",
                "roles/run.invoker",
                "roles/bigquery.jobUser",
                "roles/bigquery.dataEditor",
                "roles/storage.objectAdmin",
            ]
            import subprocess
            for role in roles:
                try:
                    subprocess.run([
                        "gcloud", "projects", "add-iam-policy-binding", project_id,
                        f"--member={principal}",
                        f"--role={role}",
                        "--condition=None"
                    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except subprocess.CalledProcessError as e:
                    logging.warning(f"Failed to bind {role} to {principal}: {e}")

    finally:
        os.chdir(original_cwd)
        shutil.rmtree(staging_dir, ignore_errors=True)

    # Process GE Deployment if flags are set
    app_id = getattr(sys.modules["__main__"].args, "app_id", None) if hasattr(sys.modules["__main__"], "args") else None
    oauth_client_id = getattr(sys.modules["__main__"].args, "oauth_client_id", None) if hasattr(sys.modules["__main__"], "args") else None
    oauth_client_secret = getattr(sys.modules["__main__"].args, "oauth_client_secret", None) if hasattr(sys.modules["__main__"], "args") else None
    ge_deploy = getattr(sys.modules["__main__"].args, "ge_deploy", False) if hasattr(sys.modules["__main__"], "args") else False

    if ge_deploy and app_id and oauth_client_id and oauth_client_secret:
        _ge_deploy(
            project=project_id,
            app_id=app_id,
            agent_name=agent_name,
            display_name=agent_name,
            description=description or "Deployed from Esmeralda",
            reasoning_engine_name=remote_agent.api_resource.name,
            oauth_client_id=oauth_client_id,
            oauth_client_secret=oauth_client_secret,
        )

    return remote_agent

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy the agent engine app to Vertex AI.")
    parser.add_argument("--project_id", default=None, help="GCP project ID")
    parser.add_argument("--location", default=None, help="GCP region")
    parser.add_argument("--agent-name", default=None, help="Name for the agent engine")
    parser.add_argument("--requirements-file", default=None, help="Path to requirements.txt file")
    parser.add_argument("--entrypoint-module", default=None, help="Entrypoint module")
    parser.add_argument("--entrypoint-object", default=None, help="Entrypoint object")
    parser.add_argument("--source-packages", nargs='*', default=None, help="Source packages to include")
    parser.add_argument("--set-env-vars", default=None, help="Comma-separated list of environment variables")
    parser.add_argument("--service-account", default=None, help="Service account email")
    parser.add_argument("--description", default=None, help="Agent description")
    parser.add_argument("--labels", default=None, help="Labels for the agent")
    parser.add_argument("--build-options", default=None, help="Build options")
    parser.add_argument("--min-instances", default=None, help="Minimum number of instances")
    parser.add_argument("--max-instances", default=None, help="Maximum number of instances")
    parser.add_argument("--resource-limits", default=None, help="Resource limits")
    parser.add_argument("--container-concurrency", default=None, help="Container concurrency")
    parser.add_argument("--agent-framework", default=None, help="Agent framework")
    
    # Network Attachment (PSC)
    parser.add_argument("--network-attachment", default=None, help="Network attachment for PSC Interface")
    parser.add_argument("--dns-peering-domain", default=None, help="DNS peering domain")
    parser.add_argument("--dns-peering-target-project", default=None, help="DNS peering target project")
    parser.add_argument("--dns-peering-target-network", default=None, help="DNS peering target network")
    
    # Agent Identity
    parser.add_argument("--enable-agent-identity", action="store_true", help="Enable agent identity (per-agent least-privilege credentials)")
    
    # Gemini Enterprise Deploy
    parser.add_argument("--ge-deploy", action="store_true", help="Register agent in Gemini Enterprise after deploy")
    parser.add_argument("--app-id", default=None, help="Gemini Enterprise engine ID")
    parser.add_argument("--oauth-client-id", default=os.environ.get("OAUTH_CLIENT_ID"), help="OAuth2 client ID")
    parser.add_argument("--oauth-client-secret", default=os.environ.get("OAUTH_CLIENT_SECRET"), help="OAuth2 client secret")

    parser.add_argument("--config-file", default=None, help="Path to agent.yaml configuration file")
    args = parser.parse_args()

    # Load configuration from file if provided
    config = {}
    if args.config_file:
        try:
            with open(args.config_file, "r") as f:
                config = yaml.safe_load(f)
                logging.info(f"Loaded configuration from {args.config_file}")
        except Exception as e:
            logging.error(f"Failed to load config file: {e}")
            sys.exit(1)

    # Helper to get value from args (high priority) or config (low priority) or default
    def get_val(arg_name, config_key, default=None):
        # Check if arg was explicitly provided (not None)
        # use getattr with default None to avoid AttributeError if arg_name doesn't exist (e.g. "dummy")
        val = getattr(args, arg_name, None)
        if val is not None:
            return val
        
        # Nested config keys (e.g. resources.cpu)
        keys = config_key.split('.')
        curr = config
        for k in keys:
            if isinstance(curr, dict) and k in curr:
                curr = curr[k]
            else:
                return default
        return curr

    # Construct arguments merging CLI and Config
    project_id = args.project_id # Project ID usually comes from CLI/Env
    location = get_val("location", "location", "us-central1")
    
    agent_name = get_val("agent_name", "name", "base-adk-agent")
    description = get_val("description", "description", "A base agent built with ADK using OTEL observability.")
    
    # Entrypoint
    entrypoint_module = get_val("entrypoint_module", "entrypoint.module", "root_agent.agent")
    entrypoint_object = get_val("entrypoint_object", "entrypoint.object", "root_agent")
    
    requirements_file = get_val("requirements_file", "requirements_file", "requirements.txt")
    
    # Resources
    # In agent.yaml: resources: {cpu: 1, memory: 4Gi}
    # In CLI: --resource-limits "{'cpu':'1'}"
    # We need to construct the dict
    resource_limits_cli = args.resource_limits
    if resource_limits_cli:
        resource_limits_val = resource_limits_cli
    else:
        # Construct from config
        cpu = get_val("dummy", "resources.cpu")
        memory = get_val("dummy", "resources.memory")
        if cpu or memory:
            resource_limits_val = str({"cpu": str(cpu) if cpu else "1", "memory": str(memory) if memory else "4Gi"})
        else:
            resource_limits_val = None

    min_instances = get_val("min_instances", "resources.min_instances")
    max_instances = get_val("max_instances", "resources.max_instances")
    container_concurrency = get_val("container_concurrency", "resources.concurrency")
    
    agent_framework = get_val("agent_framework", "framework")

    # Labels
    # CLI overrides config completely? Or merges?
    # Let's assume override or use config if CLI is None.
    # CLI labels is a string representation of dict.
    # Config labels is a dict.
    labels_cli = args.labels
    if labels_cli:
        labels_val = labels_cli
    else:
        labels_config = get_val("dummy", "labels")
        labels_val = str(labels_config) if labels_config else None

    # Env Vars
    # Merge config env with CLI env?
    # Config env is dict. CLI env is comma-string.
    # Let's parse CLI env first.
    env_vars = {}
    
    # 1. Load from YAML config (Dict)
    config_env = get_val("dummy", "env")
    if config_env and isinstance(config_env, dict):
        env_vars.update({str(k): str(v) for k, v in config_env.items()})
    
    # 2. Merge from CLI (Comma-string)
    env_cli_str = args.set_env_vars
    if env_cli_str:
        cli_vars = parse_env_vars(env_cli_str)
        env_vars.update(cli_vars)
    
    # Strip restricted variables that are natively injected/managed by Agent Engine
    restricted_env_vars = {"GOOGLE_CLOUD_LOCATION", "GOOGLE_CLOUD_PROJECT", "GOOGLE_GENAI_USE_VERTEXAI"}
    for restricted in restricted_env_vars:
        env_vars.pop(restricted, None)
        env_vars.pop(restricted.lower(), None)

    # Source Packages
    source_packages = args.source_packages
    if source_packages is None:
        # Try to get from config
        config_source = get_val("dummy", "source_packages")
        if config_source:
            if isinstance(config_source, list):
                source_packages = config_source
            else:
                source_packages = [str(config_source)]
        elif args.config_file:
             # Default if config file exists but no source_packages defined
             source_packages = ["."] 
        else:
             # Legacy default
             source_packages = ["./app"]

    deploy_agent_engine_app(
        project_id=project_id,
        location=location,
        agent_name=agent_name,
        requirements_file=requirements_file,
        entrypoint_module=entrypoint_module,
        entrypoint_object=entrypoint_object,
        source_packages=source_packages,
        class_methods_file=None,
        set_env_vars=env_vars,
        service_account=args.service_account,
        description=description,
        labels=labels_val,
        build_options=args.build_options, # Config support for this?
        min_instances=min_instances,
        max_instances=max_instances,
        resource_limits=resource_limits_val,
        container_concurrency=container_concurrency,
        agent_framework=agent_framework,
    )
