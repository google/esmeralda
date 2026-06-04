#!/usr/bin/env bash
# setup_local.sh
set -euo pipefail

echo "=================================================="
echo "🛠️  Esmeralda Local Testing Environment Setup"
echo "=================================================="

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Define the specific directories needed for local integration testing
TARGET_DIRS=(
    "agents/remotes/a2a-agent"
    "agents/root_agents/base-adk-agent"
    "agents/root_agents/network-test-agent"
    "tools_mcp"
)

for relative_dir in "${TARGET_DIRS[@]}"; do
    dir_path="$ROOT_DIR/$relative_dir"
    req_file="$dir_path/requirements.txt"
    
    if [ ! -f "$req_file" ]; then
        echo "⚠️  Skip: requirements.txt not found in $relative_dir"
        continue
    fi
    
    echo ""
    echo "📦 Setting up virtual environment for: $relative_dir"
    echo "--------------------------------------------------"
    
    (
        cd "$dir_path"
        
        # Create or repair virtual environment
        if [ ! -d ".venv" ] || [ ! -f ".venv/bin/pip" ] || ! ".venv/bin/pip" --version &>/dev/null; then
            echo "🔧 Creating/repairing .venv inside $relative_dir..."
            python3 -m venv ".venv" --clear
        else
            echo "🔄 Existing .venv found. Updating dependencies..."
        fi
        
        # Upgrade pip and install requirements
        echo "📥 Installing packages from requirements.txt..."
        ".venv/bin/pip" install --upgrade pip
        ".venv/bin/pip" install -r "requirements.txt"
        
        echo "✅ Successfully set up $relative_dir environment!"
    )
done

echo ""
echo "=================================================="
echo "🎉 Setup complete! Local testing environments configured."
echo "=================================================="
