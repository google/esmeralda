# DX Onboarding Ecosystem: Symmetric Testing (Local vs. Remote)

## 🧪 4. DX Onboarding Ecosystem: Symmetric Testing (Local vs. Remote)

Esmeralda adopts the **Symmetric Testing** philosophy. This minimizes developer friction and accelerates code validation by allowing software engineers to test AI reasoning engines offline (Inner Loop) and post-deployment in the cloud (Outer Loop) without modifying application logic.

```text
app/agents/a2a-agent/scripts/
├── test_local.py             # Offline test execution with localhost mocks
└── test_remote.py            # Real cloud integrated test execution via SSE
```

---

### A. Inner Loop: Offline Testing Architecture (`test_local.py`)

The local testing script imports the agent application object (`adk_app`) directly from the Python codebase, eliminating cloud network dependencies. It reads tool mocks running on the local loopback interface (`localhost`) across designated ports, simulating real-time asynchronous streaming:

> [!TIP]
> 📁 **Onboarding Script Available:**
> The complete Python developer script for local execution testing (Inner Loop) is available at:
> 👉 [`test_local.py`](../migration/02_workloads_and_delivery/app/agents/a2a-agent/scripts/test_local.py)


---

### B. Outer Loop: Integrated Post-Deployment Verification (`test_remote.py`)

After automated pipeline deployment, developers verify that IAM privileges, private VPC connections, and cloud database integrations operate correctly.

The `test_remote.py` script uses the native `google.auth` library to retrieve active developer credentials (with fallback to `gcloud` CLI). It resolves the production Reasoning Engine ID and initiates authenticated streaming `POST` calls via Server-Sent Events (SSE) directly against the real Vertex AI endpoint, streaming the remote agent's thought trajectory to the console:

> [!TIP]
> 📁 **Onboarding Script Available:**
> The Python developer script for triggering cloud flows and validating reasoning in GCP (Outer Loop) is available at:
> 👉 [`test_remote.py`](../migration/02_workloads_and_delivery/app/agents/a2a-agent/scripts/test_remote.py)
