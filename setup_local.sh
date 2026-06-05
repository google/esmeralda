#!/usr/bin/env bash
# setup_local.sh
set -euo pipefail

echo "=================================================="
echo "🛠️  Esmeralda Local Testing Environment Setup"
echo "=================================================="

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure PATH includes common user bin directories where uv might reside
export PATH="$PATH:$HOME/.local/bin:$HOME/bin:/usr/local/bin:/opt/homebrew/bin"

# Define the specific directories/environments needed for local integration testing
TARGET_DIRS=(
    "agents/remotes/a2a-agent"
    "agents/root_agents/base-adk-agent"
    "agents/root_agents/network-test-agent"
    "tools_mcp"
    ".locust_env"
)

# Detect or install uv for ultra-fast bootstrapping
UV_BIN=$(command -v uv || true)
if [ -z "$UV_BIN" ]; then
    echo "ℹ️  uv not found. Installing uv for ultra-fast setup..."
    if curl -LsSf https://astral.sh/uv/install.sh | sh; then
        export PATH="$HOME/.local/bin:$PATH"
        UV_BIN=$(command -v uv || true)
    fi
fi

if [ -n "$UV_BIN" ]; then
    echo "⚡ Using uv at $UV_BIN for high-speed parallel setup!"
else
    echo "⚠️  Could not install uv. Falling back to standard python3 -m venv and pip."
fi

# Create temporary status and log directories
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

setup_directory() {
    local relative_dir="$1"
    local dir_path="$ROOT_DIR/$relative_dir"
    local status_file="$TEMP_DIR/${relative_dir//\//_}.status"
    local log_file="$TEMP_DIR/${relative_dir//\//_}.log"
    
    # Handle .locust_env as a special root-level dependency
    if [ "$relative_dir" = ".locust_env" ]; then
        {
            cd "$ROOT_DIR"
            local needs_install=true
            local method="pip"
            [ -n "$UV_BIN" ] && method="uv"
            
            if [ -d ".locust_env" ] && [ -f ".locust_env/bin/locust" ]; then
                echo "SUCCESS|$method|Cached (locust already installed)" > "$status_file"
                needs_install=false
            else
                echo "Creating .locust_env..."
                if [ -n "$UV_BIN" ]; then
                    "$UV_BIN" venv .locust_env
                else
                    python3 -m venv ".locust_env" --clear
                fi
            fi
            
            if [ "$needs_install" = true ]; then
                echo "Installing locust==2.31.1..."
                if [ -n "$UV_BIN" ]; then
                    # uv pip install needs the active venv context or --python option
                    "$UV_BIN" pip install --python .locust_env/bin/python locust==2.31.1
                else
                    ".locust_env/bin/pip" install locust==2.31.1
                fi
                echo "SUCCESS|$method|Installed locust==2.31.1" > "$status_file"
            fi
        } > "$log_file" 2>&1
        
        local exit_code=$?
        if [ $exit_code -ne 0 ]; then
            local method="pip"
            [ -n "$UV_BIN" ] && method="uv"
            echo "FAILED|$method|Error (exit code: $exit_code)" > "$status_file"
            return $exit_code
        fi
        return 0
    fi
    
    local req_file="$dir_path/requirements.txt"
    if [ ! -f "$req_file" ]; then
        echo "SKIP|N/A|requirements.txt not found" > "$status_file"
        return 0
    fi
    
    {
        cd "$dir_path"
        
        # Calculate requirements checksum to detect if we can skip installing
        local req_hash=""
        if command -v md5sum &>/dev/null; then
            req_hash=$(md5sum requirements.txt | awk '{print $1}')
        elif command -v md5 &>/dev/null; then
            req_hash=$(md5 -q requirements.txt)
        else
            req_hash=$(cksum requirements.txt | awk '{print $1}')
        fi
        
        local needs_install=true
        local method="pip"
        [ -n "$UV_BIN" ] && method="uv"
        
        # Check if venv exists and is healthy
        if [ -d ".venv" ] && { [ -f ".venv/bin/python" ] || [ -f ".venv/bin/pip" ]; }; then
            # Check requirements hash
            if [ -n "$req_hash" ] && [ -f ".venv/.requirements.hash" ] && [ "$(cat .venv/.requirements.hash)" = "$req_hash" ]; then
                echo "SUCCESS|$method|Cached (requirements unchanged)" > "$status_file"
                needs_install=false
            else
                echo "Updating dependencies..."
            fi
        else
            echo "Creating virtual environment..."
            if [ -n "$UV_BIN" ]; then
                "$UV_BIN" venv .venv
            else
                python3 -m venv ".venv" --clear
            fi
        fi
        
        if [ "$needs_install" = true ]; then
            echo "Installing packages..."
            if [ -n "$UV_BIN" ]; then
                "$UV_BIN" pip install -r requirements.txt
            else
                ".venv/bin/pip" install --upgrade pip
                ".venv/bin/pip" install -r "requirements.txt"
            fi
            
            # Save checksum
            if [ -n "$req_hash" ]; then
                echo "$req_hash" > ".venv/.requirements.hash"
            fi
            echo "SUCCESS|$method|Installed/Updated dependencies" > "$status_file"
        fi
    } > "$log_file" 2>&1
    
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        local method="pip"
        [ -n "$UV_BIN" ] && method="uv"
        echo "FAILED|$method|Error (exit code: $exit_code)" > "$status_file"
        return $exit_code
    fi
}

echo "🔄 Bootstrapping environments in parallel..."
pids=()
for relative_dir in "${TARGET_DIRS[@]}"; do
    setup_directory "$relative_dir" &
    pids+=($!)
done

# Wait for all background jobs
failed=0
for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
        failed=$((failed + 1))
    fi
done

# Print beautiful summary table
echo ""
echo "=========================================================================="
echo "📊 Esmeralda Local Environment Setup Summary:"
echo "=========================================================================="
printf " %-38s | %-8s | %-6s | %s\n" "Directory" "Status" "Method" "Details"
echo "--------------------------------------------------------------------------"

for relative_dir in "${TARGET_DIRS[@]}"; do
    status_file="$TEMP_DIR/${relative_dir//\//_}.status"
    log_file="$TEMP_DIR/${relative_dir//\//_}.log"
    
    if [ -f "$status_file" ]; then
        IFS='|' read -r status method details < "$status_file"
        
        case "$status" in
            "SUCCESS")
                status_icon="✅ OK"
                ;;
            "SKIP")
                status_icon="⚠️  SKIP"
                ;;
            "FAILED")
                status_icon="❌ FAIL"
                ;;
            *)
                status_icon="❓ UNK"
                ;;
        esac
        
        printf " %-38s | %-8s | %-6s | %s\n" "$relative_dir" "$status_icon" "$method" "$details"
        
        # If it failed, print the log to help debugging
        if [ "$status" = "FAILED" ]; then
            echo ""
            echo "🔍 --- Error Log for $relative_dir ---"
            cat "$log_file"
            echo "--------------------------------------"
            echo ""
        fi
    else
        printf " %-38s | %-8s | %-6s | %s\n" "$relative_dir" "❌ FAIL" "N/A" "No status reported (killed or crashed)"
    fi
done

echo "=========================================================================="
if [ "$failed" -eq 0 ]; then
    echo "🎉 Success! All local environments are fully configured."
else
    echo "❌ Bootstrapping failed! Please see error logs above."
    exit 1
fi
echo "=========================================================================="
