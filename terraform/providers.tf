# providers.tf
# Tells Terraform which cloud to talk to and in which region.
# No credentials appear here. Terraform reads them from your environment
# (see README.md, "Configure AWS credentials").

provider "aws" {
  region = var.aws_region

  # Every resource that supports tags automatically gets these tags.
  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "Terraform"
    }
  }
}
