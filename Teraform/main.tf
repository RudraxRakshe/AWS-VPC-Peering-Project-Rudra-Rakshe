# main.tf
# Builds the VPC peering architecture:
#
#   VPC A (10.10.0.0/16)                      VPC B (10.20.0.0/16)
#   |- ewb-a-public  10.10.1.0/24  Bastion    |- ewb-b-private 10.20.1.0/24  App-B
#   |- ewb-a-private 10.10.2.0/24  App-A      |
#   '- Internet gateway                       |
#             '------ VPC peering connection ------'
#
# The file is read top to bottom: lookups, VPC A, VPC B, peering, routes,
# security groups, then instances.

# =============================================================================
# 1. Lookups and shared values
# =============================================================================

# Latest Amazon Linux 2023 (x86_64) AMI published by Amazon in the chosen region.
data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
  filter {
    name   = "root-device-type"
    values = ["ebs"]
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  # Use the AZ the user chose, otherwise the first one (ap-southeast-2a).
  az = coalesce(var.availability_zone, data.aws_availability_zones.available.names[0])
}

# =============================================================================
# 2. VPC A: public subnet, private subnet, internet gateway, route tables
# =============================================================================

resource "aws_vpc" "vpc_a" {
  cidr_block           = var.vpc_a_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = var.vpc_a_name }
}

resource "aws_internet_gateway" "igw_a" {
  vpc_id = aws_vpc.vpc_a.id # creating it with vpc_id attaches it to VPC A

  tags = { Name = "ewb-igw-a" }
}

resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.vpc_a.id
  cidr_block              = var.public_subnet_a_cidr
  availability_zone       = local.az
  map_public_ip_on_launch = true

  tags = { Name = "ewb-a-public" }
}

resource "aws_subnet" "private_a" {
  vpc_id                  = aws_vpc.vpc_a.id
  cidr_block              = var.private_subnet_a_cidr
  availability_zone       = local.az
  map_public_ip_on_launch = false

  tags = { Name = "ewb-a-private" }
}

# Public route table: internet via the gateway (the local VPC route is automatic).
resource "aws_route_table" "public_a" {
  vpc_id = aws_vpc.vpc_a.id

  tags = { Name = "ewb-rt-public" }
}

resource "aws_route_table" "private_a" {
  vpc_id = aws_vpc.vpc_a.id

  tags = { Name = "ewb-rt-a-private" }
}

resource "aws_route" "public_a_to_internet" {
  route_table_id         = aws_route_table.public_a.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.igw_a.id
}

resource "aws_route_table_association" "public_a" {
  subnet_id      = aws_subnet.public_a.id
  route_table_id = aws_route_table.public_a.id
}

resource "aws_route_table_association" "private_a" {
  subnet_id      = aws_subnet.private_a.id
  route_table_id = aws_route_table.private_a.id
}

# =============================================================================
# 3. VPC B: private subnet and route table (no internet gateway, no NAT)
# =============================================================================

resource "aws_vpc" "vpc_b" {
  cidr_block           = var.vpc_b_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = var.vpc_b_name }
}

resource "aws_subnet" "private_b" {
  vpc_id                  = aws_vpc.vpc_b.id
  cidr_block              = var.private_subnet_b_cidr
  availability_zone       = local.az
  map_public_ip_on_launch = false

  tags = { Name = "ewb-b-private" }
}

resource "aws_route_table" "private_b" {
  vpc_id = aws_vpc.vpc_b.id

  tags = { Name = "ewb-rt-b-private" }
}

resource "aws_route_table_association" "private_b" {
  subnet_id      = aws_subnet.private_b.id
  route_table_id = aws_route_table.private_b.id
}

# =============================================================================
# 4. VPC peering and routes in both directions
# =============================================================================

# Same account and same region, so Terraform can accept the request itself.
resource "aws_vpc_peering_connection" "a_to_b" {
  vpc_id      = aws_vpc.vpc_a.id
  peer_vpc_id = aws_vpc.vpc_b.id
  auto_accept = true

  tags = { Name = "ewb-vpc-a-to-vpc-b" }

  # Stop early if the two VPC ranges overlap (one contains the other's start).
  lifecycle {
    precondition {
      condition     = !(cidrcontains(var.vpc_a_cidr, cidrhost(var.vpc_b_cidr, 0)) || cidrcontains(var.vpc_b_cidr, cidrhost(var.vpc_a_cidr, 0)))
      error_message = "vpc_a_cidr and vpc_b_cidr overlap. Peered VPCs must use non-overlapping CIDR ranges."
    }
  }
}

# Routes toward VPC B (the bastion's public table and App-A's private table).
resource "aws_route" "public_a_to_vpc_b" {
  route_table_id            = aws_route_table.public_a.id
  destination_cidr_block    = var.vpc_b_cidr
  vpc_peering_connection_id = aws_vpc_peering_connection.a_to_b.id
}

resource "aws_route" "private_a_to_vpc_b" {
  route_table_id            = aws_route_table.private_a.id
  destination_cidr_block    = var.vpc_b_cidr
  vpc_peering_connection_id = aws_vpc_peering_connection.a_to_b.id
}

# Return route toward VPC A. Without it, App-B could receive requests but its replies would be lost.
resource "aws_route" "private_b_to_vpc_a" {
  route_table_id            = aws_route_table.private_b.id
  destination_cidr_block    = var.vpc_a_cidr
  vpc_peering_connection_id = aws_vpc_peering_connection.a_to_b.id
}

# =============================================================================
# 5. Security groups (rules are separate resources, which avoids circular references)
# =============================================================================

# ---- Bastion: SSH only from the administrator's IP ----
resource "aws_security_group" "bastion" {
  name        = "ewb-sg-bastion"
  description = "Bastion host - SSH only from the administrator IP"
  vpc_id      = aws_vpc.vpc_a.id

  tags = { Name = "ewb-sg-bastion" }
}

resource "aws_vpc_security_group_ingress_rule" "bastion_ssh_from_admin" {
  security_group_id = aws_security_group.bastion.id
  description       = "SSH from the administrator public IP"
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = var.admin_ip_cidr
}

resource "aws_vpc_security_group_egress_rule" "bastion_all_out" {
  security_group_id = aws_security_group.bastion.id
  description       = "Allow all outbound traffic"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# ---- App-A: SSH from the bastion (same VPC, so a security-group reference works) ----
resource "aws_security_group" "app_a" {
  name        = "ewb-sg-app-a"
  description = "App-A - SSH from bastion, ping from App-B"
  vpc_id      = aws_vpc.vpc_a.id

  tags = { Name = "ewb-sg-app-a" }
}

resource "aws_vpc_security_group_ingress_rule" "app_a_ssh_from_bastion" {
  security_group_id            = aws_security_group.app_a.id
  description                  = "SSH from the bastion security group"
  ip_protocol                  = "tcp"
  from_port                    = 22
  to_port                      = 22
  referenced_security_group_id = aws_security_group.bastion.id
}

resource "aws_vpc_security_group_ingress_rule" "app_a_icmp_from_app_b" {
  security_group_id = aws_security_group.app_a.id
  description       = "ICMP echo request (ping) from App-B only"
  ip_protocol       = "icmp"
  from_port         = 8  # ICMP type 8 = echo request
  to_port           = -1 # any ICMP code
  cidr_ipv4         = "${var.app_b_private_ip}/32"
}

resource "aws_vpc_security_group_egress_rule" "app_a_all_out" {
  security_group_id = aws_security_group.app_a.id
  description       = "Allow all outbound traffic"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# ---- App-B: the bastion is in another VPC, so its exact private IP (/32) is used ----
resource "aws_security_group" "app_b" {
  name        = "ewb-sg-app-b"
  description = "App-B - SSH from bastion, ping from App-A"
  vpc_id      = aws_vpc.vpc_b.id

  tags = { Name = "ewb-sg-app-b" }
}

resource "aws_vpc_security_group_ingress_rule" "app_b_ssh_from_bastion" {
  security_group_id = aws_security_group.app_b.id
  description       = "SSH from the bastion private IP (across the peering connection)"
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = "${var.bastion_private_ip}/32"
}

resource "aws_vpc_security_group_ingress_rule" "app_b_icmp_from_app_a" {
  security_group_id = aws_security_group.app_b.id
  description       = "ICMP echo request (ping) from App-A only"
  ip_protocol       = "icmp"
  from_port         = 8
  to_port           = -1
  cidr_ipv4         = "${var.app_a_private_ip}/32"
}

resource "aws_vpc_security_group_egress_rule" "app_b_all_out" {
  security_group_id = aws_security_group.app_b.id
  description       = "Allow all outbound traffic"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# =============================================================================
# 6. EC2 instances (Amazon Linux 2023, encrypted 8 GiB gp3 root volume)
# =============================================================================

resource "aws_instance" "bastion" {
  ami                         = data.aws_ami.amazon_linux_2023.id
  instance_type               = var.instance_type
  key_name                    = var.key_name
  subnet_id                   = aws_subnet.public_a.id
  private_ip                  = var.bastion_private_ip
  vpc_security_group_ids      = [aws_security_group.bastion.id]
  associate_public_ip_address = true

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required" # IMDSv2 only
  }

  root_block_device {
    volume_size           = 8
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  tags = { Name = "ewb-bastion" }

  # The public IP is only reachable once the internet route and subnet association exist.
  depends_on = [
    aws_route.public_a_to_internet,
    aws_route_table_association.public_a,
  ]

  lifecycle {
    precondition {
      condition     = cidrcontains(var.public_subnet_a_cidr, var.bastion_private_ip)
      error_message = "bastion_private_ip must be inside public_subnet_a_cidr."
    }
  }
}

resource "aws_instance" "app_a" {
  ami                         = data.aws_ami.amazon_linux_2023.id
  instance_type               = var.instance_type
  key_name                    = var.key_name
  subnet_id                   = aws_subnet.private_a.id
  private_ip                  = var.app_a_private_ip
  vpc_security_group_ids      = [aws_security_group.app_a.id]
  associate_public_ip_address = false

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    volume_size           = 8
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  tags = { Name = "ewb-app-a" }

  depends_on = [aws_route_table_association.private_a]

  lifecycle {
    precondition {
      condition     = cidrcontains(var.private_subnet_a_cidr, var.app_a_private_ip)
      error_message = "app_a_private_ip must be inside private_subnet_a_cidr."
    }
  }
}

resource "aws_instance" "app_b" {
  ami                         = data.aws_ami.amazon_linux_2023.id
  instance_type               = var.instance_type
  key_name                    = var.key_name
  subnet_id                   = aws_subnet.private_b.id
  private_ip                  = var.app_b_private_ip
  vpc_security_group_ids      = [aws_security_group.app_b.id]
  associate_public_ip_address = false

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    volume_size           = 8
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  tags = { Name = "ewb-app-b" }

  depends_on = [aws_route_table_association.private_b]

  lifecycle {
    precondition {
      condition     = cidrcontains(var.private_subnet_b_cidr, var.app_b_private_ip)
      error_message = "app_b_private_ip must be inside private_subnet_b_cidr."
    }
  }
}
