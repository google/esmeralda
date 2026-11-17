// Generated from apps/frontends/esmeralda-ui/openapi.json by `npm run gen:types`. Do not edit.
// The BFF regenerates openapi.json with `python -m esmeralda_ui.openapi`.

export interface paths {
    "/api/audit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Audit */
        get: operations["get_audit_api_audit_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/catalog": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Catalog */
        get: operations["get_catalog_api_catalog_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/catalog/{target}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Target */
        get: operations["get_target_api_catalog__target__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Me */
        get: operations["me_api_me_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/traces": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Traces */
        get: operations["list_traces_api_traces_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/traces/{trace_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Trace */
        get: operations["get_trace_api_traces__trace_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/usage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Usage */
        get: operations["get_usage_api_usage_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/healthz": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Healthz */
        get: operations["healthz_healthz_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** Audit */
        Audit: {
            /** Days */
            days: number;
            /** Entries */
            entries: components["schemas"]["AuditEntry"][];
            /**
             * Source
             * @enum {string}
             */
            source: "bigquery" | "demo";
        };
        /** AuditEntry */
        AuditEntry: {
            /** Method Name */
            method_name: string | null;
            /** Principal Email */
            principal_email: string | null;
            /** Resource Name */
            resource_name: string | null;
            /** Service Name */
            service_name: string | null;
            /** Status Message */
            status_message: string | null;
            /**
             * Timestamp
             * Format: date-time
             */
            timestamp: string;
        };
        /** Catalog */
        Catalog: {
            /** Environment */
            environment: string;
            policy: components["schemas"]["CatalogPolicy"];
            /**
             * Resolved File
             * @description Repository path of the resolved catalog the BFF serves
             */
            resolved_file: string;
            /** Resolved Sha256 */
            resolved_sha256: string;
            /** Targets */
            targets: components["schemas"]["CatalogTarget"][];
            /** Teams */
            teams: components["schemas"]["CatalogTeam"][];
        };
        /** CatalogCaller */
        CatalogCaller: {
            /** Caller */
            caller: string;
            /** Enforced */
            enforced: boolean;
            /** Gateway */
            gateway: string | null;
            /** Identity */
            identity: string;
            pays: components["schemas"]["Payer"];
            /** Product */
            product?: string | null;
            /** Team */
            team: string | null;
            /** Tools */
            tools?: string[] | null;
        };
        /** CatalogPolicy */
        CatalogPolicy: {
            /** Declared Mismatch */
            declared_mismatch?: string | null;
            /** Unmapped Caller */
            unmapped_caller?: string | null;
        };
        /** CatalogTarget */
        CatalogTarget: {
            /** Callers */
            callers: components["schemas"]["CatalogCaller"][];
            /** Compiled */
            compiled: boolean;
            /** Deploy */
            deploy: string | null;
            /** Description */
            description: string | null;
            /** Example */
            example: boolean;
            /** Gateway */
            gateway: string | null;
            /** Google Apis */
            google_apis?: string[] | null;
            /** Ingress Backend */
            ingress_backend: string | null;
            /** Kind */
            kind: string;
            /** Name */
            name: string;
            /** Owner Team */
            owner_team: string;
            /** Registry */
            registry: string | null;
        };
        /** CatalogTeam */
        CatalogTeam: {
            /** Cost Centers */
            cost_centers: string[];
            /** External */
            external: boolean;
            /** Name */
            name: string;
        };
        /** ChargebackRow */
        ChargebackRow: {
            /** Agent Id */
            agent_id: string | null;
            /** Attribution */
            attribution: string | null;
            /** Billed Team */
            billed_team: string | null;
            /**
             * Billing Month
             * Format: date-time
             */
            billing_month: string;
            /** Cache Hit Ratio Pct */
            cache_hit_ratio_pct: number | null;
            /** Cached Prompt Tokens */
            cached_prompt_tokens: number | null;
            /** Gateway Metered Requests */
            gateway_metered_requests: number | null;
            /** Model */
            model: string | null;
            /** Net Total Chargeback Usd */
            net_total_chargeback_usd: number;
            /** Total Llm Calls */
            total_llm_calls: number | null;
            /** Total Requests */
            total_requests: number;
            /** Total Tokens */
            total_tokens: number;
            /** Verified */
            verified: boolean | null;
            /** Workload Team */
            workload_team: string | null;
        };
        /** ErrorBody */
        ErrorBody: {
            /**
             * Error
             * @description Generic error message (no internal detail)
             */
            error: string;
        };
        /** Health */
        Health: {
            /**
             * Status
             * @constant
             */
            status: "ok";
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** Me */
        Me: {
            /**
             * Auth
             * @description iap: verified IAP JWT; local-dev: fixed local identity
             * @enum {string}
             */
            auth: "iap" | "local-dev";
            /**
             * Data Source
             * @enum {string}
             */
            data_source: "bigquery" | "demo" | "unconfigured";
            /**
             * Demo
             * @description True when the BFF serves synthetic data (explicit demo mode)
             */
            demo: boolean;
            /** Email */
            email: string;
            /** Environment */
            environment: string;
            /** Subject */
            subject: string;
        };
        /** Payer */
        Payer: {
            /** Cost Centers */
            cost_centers: string[];
            /** Team */
            team: string | null;
        };
        /** TeamTotal */
        TeamTotal: {
            /** Billed Team */
            billed_team: string;
            /** Net Total Chargeback Usd */
            net_total_chargeback_usd: number;
            /** Total Requests */
            total_requests: number;
            /** Total Tokens */
            total_tokens: number;
        };
        /** Trace */
        Trace: {
            /** Duration Ms */
            duration_ms: number;
            /**
             * Source
             * @enum {string}
             */
            source: "bigquery" | "demo";
            /** Spans */
            spans: components["schemas"]["TraceSpan"][];
            /**
             * Started At
             * Format: date-time
             */
            started_at: string;
            /** Total Tokens */
            total_tokens: number;
            /** Trace Id */
            trace_id: string;
        };
        /** TraceList */
        TraceList: {
            /** Days */
            days: number;
            /**
             * Source
             * @enum {string}
             */
            source: "bigquery" | "demo";
            /** Traces */
            traces: components["schemas"]["TraceSummary"][];
        };
        /** TraceSpan */
        TraceSpan: {
            /** Caller Principal */
            caller_principal: string | null;
            /** Caller Team */
            caller_team: string | null;
            /** Caller Verified */
            caller_verified: boolean | null;
            /**
             * Duration Ms
             * @description latency_ms of the record (0 when the record has none)
             */
            duration_ms: number;
            /** Error Type */
            error_type: string | null;
            /** Event */
            event: string | null;
            /** Gateway Name */
            gateway_name: string | null;
            /** Gateway Product */
            gateway_product: string | null;
            /** Http Status Code */
            http_status_code: number | null;
            /** Input Tokens */
            input_tokens: number | null;
            /** Model */
            model: string | null;
            /**
             * Offset Ms
             * @description Start of the span, relative to the start of the trace
             */
            offset_ms: number;
            /** Output Tokens */
            output_tokens: number | null;
            /** Source */
            source: string | null;
            /** Span Id */
            span_id: string | null;
            /** Status */
            status: string | null;
            /**
             * Timestamp
             * Format: date-time
             */
            timestamp: string;
            /** Tool Name */
            tool_name: string | null;
            /** Tool Server */
            tool_server: string | null;
            /** Tool Status */
            tool_status: string | null;
            /** Workload Name */
            workload_name: string | null;
            /** Workload Team */
            workload_team: string | null;
        };
        /** TraceSummary */
        TraceSummary: {
            /** Agents */
            agents: string[];
            /** Billed Team */
            billed_team: string | null;
            /**
             * Started At
             * Format: date-time
             */
            started_at: string;
            /** Total Tokens */
            total_tokens: number;
            /** Trace Id */
            trace_id: string;
        };
        /** Usage */
        Usage: {
            /**
             * Month
             * @description YYYY-MM
             */
            month: string;
            /** Rows */
            rows: components["schemas"]["ChargebackRow"][];
            /**
             * Source
             * @enum {string}
             */
            source: "bigquery" | "demo";
            /** Team */
            team: string | null;
            /** Totals */
            totals: components["schemas"]["TeamTotal"][];
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    get_audit_api_audit_get: {
        parameters: {
            query?: {
                days?: number;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Audit"];
                };
            };
            /** @description Missing or invalid IAP identity */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description Data source unavailable or not configured */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    get_catalog_api_catalog_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Catalog"];
                };
            };
            /** @description Missing or invalid IAP identity */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Data source unavailable or not configured */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    get_target_api_catalog__target__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                target: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CatalogTarget"];
                };
            };
            /** @description Missing or invalid IAP identity */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unknown target */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description Data source unavailable or not configured */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    me_api_me_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Me"];
                };
            };
            /** @description Missing or invalid IAP identity */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Data source unavailable or not configured */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    list_traces_api_traces_get: {
        parameters: {
            query?: {
                days?: number;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TraceList"];
                };
            };
            /** @description Missing or invalid IAP identity */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description Data source unavailable or not configured */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    get_trace_api_traces__trace_id__get: {
        parameters: {
            query?: {
                days?: number;
            };
            header?: never;
            path: {
                /** @description W3C trace id, 32 lowercase hex */
                trace_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Trace"];
                };
            };
            /** @description Missing or invalid IAP identity */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description No usage records for this trace */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description Data source unavailable or not configured */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    get_usage_api_usage_get: {
        parameters: {
            query?: {
                /** @description YYYY-MM (default: this month, UTC) */
                month?: string | null;
                /** @description Only this billed team */
                team?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Usage"];
                };
            };
            /** @description Missing or invalid IAP identity */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description Data source unavailable or not configured */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    healthz_healthz_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Health"];
                };
            };
        };
    };
}
