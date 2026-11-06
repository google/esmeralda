# infrastructure/modules/3-security/pki.tf
# ====================================================================
# 4. INTERNAL PKI: ROOT CA + ENTRY POINT CERTIFICATE FOR *.<internal_domain>
# --------------------------------------------------------------------
# The Root CA and its private key never leave this layer. Consumers only get public material:
#   - layer 4 governance: Agent Gateway TrustConfig + agent CA bundle (internal_ca_cert_pem)
#   - layer 5 entrypoint: the regional SSL certificate below (entrypoint_ssl_certificate_id),
#                         a leaf for <internal_domain> + *.<internal_domain> signed here
# ====================================================================

resource "tls_private_key" "internal_ca" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "internal_ca" {
  private_key_pem   = tls_private_key.internal_ca.private_key_pem
  is_ca_certificate = true

  subject {
    common_name  = "Esmeralda Internal Root CA"
    organization = "Esmeralda Internal"
  }

  validity_period_hours = 87600 # 10 years

  allowed_uses = [
    "cert_signing",
    "crl_signing",
    "digital_signature",
    "key_encipherment",
  ]
}

# Public CA certificate only (never the key), published for operators and test clients.
resource "google_secret_manager_secret" "internal_ca" {
  secret_id = "esmeralda-internal-root-ca-${var.environment}"
  project   = var.gateway_project_id

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "internal_ca" {
  secret      = google_secret_manager_secret.internal_ca.id
  secret_data = tls_self_signed_cert.internal_ca.cert_pem
}

# --------------------------------------------------------------------
# Entry point leaf: <internal_domain> + *.<internal_domain>, signed by the Root CA above.
# The wildcard covers every <svc>.<internal_domain>, so adding a service or gateway never
# reissues the certificate. Loaded on the layer-5 entry point HTTPS proxy.
# --------------------------------------------------------------------
resource "tls_private_key" "entrypoint" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_cert_request" "entrypoint" {
  private_key_pem = tls_private_key.entrypoint.private_key_pem

  subject {
    common_name  = "*.${var.internal_domain}"
    organization = "Esmeralda Internal"
  }

  dns_names = [
    var.internal_domain,
    "*.${var.internal_domain}",
  ]
}

resource "tls_locally_signed_cert" "entrypoint" {
  cert_request_pem   = tls_cert_request.entrypoint.cert_request_pem
  ca_private_key_pem = tls_private_key.internal_ca.private_key_pem
  ca_cert_pem        = tls_self_signed_cert.internal_ca.cert_pem

  validity_period_hours = 8760 # 1 year (complies with Chromium <= 398 days check)

  allowed_uses = [
    "key_encipherment",
    "digital_signature",
    "server_auth",
  ]
}

# Leaf + CA chain as a regional SSL certificate in the gateway project, where the entry point
# load balancer runs. name_prefix + create_before_destroy lets a renewal swap it on the proxy
# without downtime.
resource "google_compute_region_ssl_certificate" "entrypoint" {
  name_prefix = "cert-esmeralda-ilb-"
  project     = var.gateway_project_id
  region      = var.region
  private_key = tls_private_key.entrypoint.private_key_pem
  certificate = "${tls_locally_signed_cert.entrypoint.cert_pem}${tls_self_signed_cert.internal_ca.cert_pem}"

  lifecycle {
    create_before_destroy = true
  }
}
