# versions.tf
# Pins the Terraform CLI version and the AWS provider version so everyone who
# runs this project gets the same behaviour.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40" # any 5.x release from 5.40 upwards, but not 6.x
    }
  }
}
