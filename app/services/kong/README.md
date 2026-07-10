# Kong API Gateway Service
This directory contains the container packaging and runtime configuration for Kong Gateway running as a serverless Cloud Run v2 service inside the Esmeralda Shared VPC.

## Deployment
This service is built and pushed to Artifact Registry via `make build-services` and deployed via `make deploy-gateway` (or `make deploy-services`).
