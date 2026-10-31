# variables.tf
# All settings you may want to change live here. Defaults match the manually
# built project, so you only HAVE to supply key_name and admin_ip_cidr.

# ---------------------------------------------------------------- General
variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "ap-southeast-2" # Sydney
}

variable "project_name" {
  description = "Value of the Project tag applied to all resources."
  type        = string
  default     = "ewb-vpc-peering"
}

variable "availability_zone" {
  description = "Availability Zone for all subnets. Leave null to use the first available AZ in the region (ap-southeast-2a in Sydney)."
  type        = string
  default     = null
}

# ---------------------------------------------------------------- Access
variable "key_name" {
  description = "Name of an EXISTING EC2 key pair in the chosen region. Only the name is needed; never put private key contents in Terraform files."
  type        = string
}

variable "admin_ip_cidr" {
  description = "Your public IPv4 address in /32 form (for example 203.0.113.25/32). Only this address may SSH to the bastion."
  type        = string

  validation {
    condition     = can(cidrhost(var.admin_ip_cidr, 0)) && endswith(var.admin_ip_cidr, "/32")
    error_message = "admin_ip_cidr must be a single IPv4 address in /32 form, for example 203.0.113.25/32. 0.0.0.0/0 is not allowed."
  }
}

# ---------------------------------------------------------------- Compute
variable "instance_type" {
  description = "EC2 instance type for all three instances (x86_64, to match the AMI lookup)."
  type        = string
  default     = "t3.micro"
}

# ---------------------------------------------------------------- VPC A
variable "vpc_a_name" {
  description = "Name tag of VPC A."
  type        = string
  default     = "ewb-vpc-a"
}

variable "vpc_a_cidr" {
  description = "CIDR block of VPC A."
  type        = string
  default     = "10.10.0.0/16"
}

variable "public_subnet_a_cidr" {
  description = "CIDR block of the public subnet in VPC A (ewb-a-public)."
  type        = string
  default     = "10.10.1.0/24"
}

variable "private_subnet_a_cidr" {
  description = "CIDR block of the private subnet in VPC A (ewb-a-private)."
  type        = string
  default     = "10.10.2.0/24"
}

# ---------------------------------------------------------------- VPC B
variable "vpc_b_name" {
  description = "Name tag of VPC B."
  type        = string
  default     = "ewb-vpc-b"
}

variable "vpc_b_cidr" {
  description = "CIDR block of VPC B. Must not overlap vpc_a_cidr."
  type        = string
  default     = "10.20.0.0/16"
}

variable "private_subnet_b_cidr" {
  description = "CIDR block of the private subnet in VPC B (ewb-b-private)."
  type        = string
  default     = "10.20.1.0/24"
}

# ---------------------------------------------------------------- Fixed private IPs
variable "bastion_private_ip" {
  description = "Fixed private IP of the bastion (must be inside public_subnet_a_cidr)."
  type        = string
  default     = "10.10.1.10"
}

variable "app_a_private_ip" {
  description = "Fixed private IP of App-A (must be inside private_subnet_a_cidr)."
  type        = string
  default     = "10.10.2.10"
}

variable "app_b_private_ip" {
  description = "Fixed private IP of App-B (must be inside private_subnet_b_cidr)."
  type        = string
  default     = "10.20.1.10"
}
