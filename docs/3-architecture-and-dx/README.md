# Architecture Strategy & Developer Experience (DX)

This section of the documentation unifies cross-cutting architectural strategies and Developer Experience (DX) patterns, including the BYOInfra (Bring Your Own Infrastructure) fallback pattern, declarative Terragrunt automation, and symmetric testing ecosystems.

---

## 🗺️ Architecture & DX Index

1. **[Greenfield vs. Brownfield (BYOInfra) Pattern](./01-byoinfra-pattern.md)**
   - Client Environment Parameters (`live/client-prod/env.yaml`)
   - Dynamically Skipping Infrastructure Stages (`skip` block)
   - Downstream Fallback Lookups
2. **[Declarative Automation & Configuration Sovereignty](./02-declarative-automation.md)**
   - Declarative Automation without `deploy.sh`
   - Configuration Sovereignty without Local `.env` Files
3. **[DX Onboarding Ecosystem: Symmetric Testing](./03-symmetric-testing.md)**
   - Inner Loop: Offline Testing Architecture (`test_local.py`)
   - Outer Loop: Integrated Post-Deployment Verification (`test_remote.py`)
