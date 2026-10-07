# infrastructure/modules/3-security/pki.tf
# ====================================================================
# 4. INTERNAL PKI: ROOT CA FOR *.esmeralda.internal
# --------------------------------------------------------------------
# Owned by the security layer so both consumers depend on it explicitly:
#   - layer 4 governance: Agent Gateway TrustConfig + agent CA bundle
#   - layer 5 workloads:  Kong signs its *.esmeralda.internal ILB leaf with it
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
