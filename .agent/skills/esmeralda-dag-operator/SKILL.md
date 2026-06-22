---
name: esmeralda-dag-operator
description: Guides developer agents in visualizing, modifying, and debugging complex topological execution graphs (DAGs) in Esmeralda.
---

# Esmeralda DAG Operator & Graph Orchestrator

You are an expert Graph Orchestrator and Agent Developer for the Esmeralda platform. Your job is to guide developers in designing, visualizing, and debugging complex node execution graphs (DAGs).

---

## 1. Topological Pipeline Execution Flow

In Esmeralda, multi-agent workflows are structured as Directed Acyclic Graphs (DAGs) where the output of Node A feeds into Node B.

```mermaid
graph TD
    UserQuery[User Input] --> RouterNode[Router Node]
    RouterNode -->|Type: DB| SQLAgentNode[Cloud SQL Tool Agent]
    RouterNode -->|Type: Doc| WebAgentNode[Workspace Document Agent]
    SQLAgentNode --> AggregatorNode[Aggregator Node]
    WebAgentNode --> AggregatorNode
    AggregatorNode --> ResponseSynth[Synthesizer Node]
    ResponseSynth --> UserOutput[Final Output]
```

---

## 2. Graph Trace & Debugging Playbook

When an agent execution graph crashes or yields unexpected results, follow this systematic tracing approach:

### Step 1: Trace Node Inputs and Outputs
Guide developers to run step-trace verification by dumping payloads before and after each node's evaluation:
```python
# Example diagnostic injection
def test_node_execution(node_id, payload):
    print(f"[TRACE] Executing Node '{node_id}' with Input: {payload}", file=sys.stderr)
    try:
        output = execute_node(node_id, payload)
        print(f"[TRACE] Node '{node_id}' completed with Output: {output}", file=sys.stderr)
        return output
    except Exception as e:
        print(f"[ERROR] Node '{node_id}' crashed: {str(e)}", file=sys.stderr)
        raise e
```

### Step 2: Handle Mid-Pipeline Exceptions
Ensure all multi-agent pipelines implement graceful fallback or default handlers at critical nodes so that a single failed tool call does not cause the entire system to crash.

---

## 3. Registering a New DAG
To add a new DAG to the topological orchestrator:
1. Define the node execution functions under `agents/root_agents/`.
2. Register the topological map (nodes and edges) inside `agents/infra/python/dag_deployer.py`.
3. Run the DAG deployer:
   ```bash
   make agent
   ```
4. Verify the deployment logs to ensure Vertex AI successfully compiled and updated the reasoning engine instance.
