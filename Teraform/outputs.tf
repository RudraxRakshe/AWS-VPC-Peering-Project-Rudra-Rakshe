# outputs.tf
# Values printed after "terraform apply". Show them again any time with "terraform output".

output "vpc_a_id" {
  description = "ID of VPC A (ewb-vpc-a)."
  value       = aws_vpc.vpc_a.id
}

output "vpc_b_id" {
  description = "ID of VPC B (ewb-vpc-b)."
  value       = aws_vpc.vpc_b.id
}

output "public_subnet_a_id" {
  description = "ID of ewb-a-public."
  value       = aws_subnet.public_a.id
}

output "private_subnet_a_id" {
  description = "ID of ewb-a-private."
  value       = aws_subnet.private_a.id
}

output "private_subnet_b_id" {
  description = "ID of ewb-b-private."
  value       = aws_subnet.private_b.id
}

output "peering_connection_id" {
  description = "ID of the VPC peering connection."
  value       = aws_vpc_peering_connection.a_to_b.id
}

output "bastion_instance_id" {
  description = "Instance ID of the bastion."
  value       = aws_instance.bastion.id
}

output "bastion_public_ip" {
  description = "Public IPv4 address of the bastion (changes if the instance is stopped and started)."
  value       = aws_instance.bastion.public_ip
}

output "app_a_private_ip" {
  description = "Private IP of App-A."
  value       = aws_instance.app_a.private_ip
}

output "app_b_private_ip" {
  description = "Private IP of App-B."
  value       = aws_instance.app_b.private_ip
}

output "bastion_security_group_id" {
  description = "ID of ewb-sg-bastion."
  value       = aws_security_group.bastion.id
}

output "app_a_security_group_id" {
  description = "ID of ewb-sg-app-a."
  value       = aws_security_group.app_a.id
}

output "app_b_security_group_id" {
  description = "ID of ewb-sg-app-b."
  value       = aws_security_group.app_b.id
}
