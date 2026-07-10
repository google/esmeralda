# Deploy a private test VM inside the Shared VPC network for connectivity verification
resource "google_compute_instance" "test_vm" {
  name         = "test-vm-${var.environment}"
  machine_type = "e2-micro"
  zone         = var.zone
  project      = var.project_id

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 10
    }
  }

  network_interface {
    subnetwork = var.subnet_id
    # No access_config block here to ensure the VM is private (no external IP)
  }

  service_account {
    email  = var.service_account_email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  # Allow instance termination on destroy
  allow_stopping_for_update = true
}

