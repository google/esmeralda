output "instance_name" {
  value       = google_compute_instance.test_vm.name
  description = "The name of the test VM instance"
}

output "instance_zone" {
  value       = google_compute_instance.test_vm.zone
  description = "The zone where the test VM is deployed"
}

output "instance_ip" {
  value       = google_compute_instance.test_vm.network_interface[0].network_ip
  description = "The internal IP of the test VM"
}
