# MetaBare dev environment.
#
# Read the cost table in the README before applying. This creates about
# $144/month of fixed cost, most of it the EKS control plane and one always-on
# node, and it accrues whether or not anything is ingested.
#
# `make destroy ENV=dev` removes everything here. Destroying between sessions
# is the intended workflow, not an emergency measure.

locals {
  name = "metabare-${var.environment}"

  # Cost allocation tags. These must be activated in the
  # Billing console before they appear in Cost Explorer or CUR data, and
  # activation is not retroactive, which is why they are applied from the
  # first apply rather than added later.
  tags = {
    Project     = "metabare"
    Environment = var.environment
    ManagedBy   = "terraform"
    Owner       = var.owner
    Repository  = "github.com/gordonmurray/metabare.com"
  }

  azs = slice(data.aws_availability_zones.available.names, 0, var.availability_zone_count)

  # Public subnets are where nodes run when enable_nat_gateway is false.
  # /20 each gives 4091 usable addresses per subnet, which matters because the
  # VPC CNI assigns a secondary IP per pod. A /27 is the classic way to make
  # Karpenter mysteriously fail to provision.
  public_subnets  = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, i)]
  private_subnets = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, i + 8)]
}

data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Network
#
# No NAT Gateway in dev. An S3 gateway endpoint is free and carries the
# workload's dominant dependency, so a gateway would be a quarter of the
# platform bill for very little.
# ---------------------------------------------------------------------------

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.6"

  name = local.name
  cidr = var.vpc_cidr
  azs  = local.azs

  public_subnets  = local.public_subnets
  private_subnets = var.enable_nat_gateway ? local.private_subnets : []

  # cost-acknowledged: false in dev. Flipping this to true adds ~$35.04/month
  # plus $0.048/GB, and the README cost table must be updated with it.
  enable_nat_gateway = var.enable_nat_gateway
  single_nat_gateway = var.enable_nat_gateway

  enable_dns_hostnames = true
  enable_dns_support   = true

  # Nodes need a public IP when there is no NAT Gateway. Security groups allow
  # no inbound traffic; this is egress only.
  map_public_ip_on_launch = !var.enable_nat_gateway

  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }

  tags = local.tags
}

# S3 gateway endpoint. Not billed: no hourly charge and no data processing
# charge, unlike an interface endpoint at $0.011/hour per AZ. This is what
# makes skipping the NAT Gateway reasonable rather than merely cheap, since
# S3 is the workload's dominant dependency.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat(module.vpc.public_route_table_ids, module.vpc.private_route_table_ids)

  tags = merge(local.tags, { Name = "${local.name}-s3", Component = "network" })
}

# ---------------------------------------------------------------------------
# EKS
# ---------------------------------------------------------------------------

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.24"

  name               = local.name
  kubernetes_version = var.kubernetes_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = var.enable_nat_gateway ? module.vpc.private_subnets : module.vpc.public_subnets

  # Public endpoint because there is no NAT and no bastion; access is
  # controlled by IAM, not by network reachability. A production deployment
  # would restrict public_access_cidrs or use a private endpoint with a
  # connectivity path.
  endpoint_public_access  = true
  endpoint_private_access = true

  # Control-plane logging is OFF. At $0.57/GB ingested it is easy to leave on
  # and quietly become a top-three line item on a cluster that is otherwise
  # nearly idle. Turn it on deliberately, for a bounded period, when debugging.
  enabled_log_types = []

  # API-only authentication. aws-auth ConfigMap management is deprecated and
  # access entries are both auditable and manageable in Terraform.
  authentication_mode                      = "API"
  enable_cluster_creator_admin_permissions = true

  access_entries = {
    for arn in var.cluster_admin_principals : replace(basename(arn), ":", "-") => {
      principal_arn = arn
      policy_associations = {
        admin = {
          policy_arn = "arn:aws:eks:::cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = {
            type = "cluster"
          }
        }
      }
    }
  }

  addons = {
    coredns                = {}
    kube-proxy             = {}
    vpc-cni                = { before_compute = true }
    eks-pod-identity-agent = { before_compute = true }
  }

  eks_managed_node_groups = {
    # Stable On-Demand capacity for everything that must stay available:
    # Firn, the API, the query encoder, and later Karpenter, KEDA and the
    # observability stack.
    #
    # Deliberately one node. Premature high availability in a lab costs real
    # money for a resilience property nothing here needs. What would change
    # for production: min_size 2 across AZs, and a PodDisruptionBudget on Firn.
    system = {
      instance_types = [var.stable_node_instance_type]
      capacity_type  = "ON_DEMAND"

      min_size     = 1
      max_size     = 2
      desired_size = 1

      # One AZ. Cross-AZ data transfer between a pod and Firn would otherwise
      # be billed at $0.01/GB in each direction for no benefit here.
      subnet_ids = [
        var.enable_nat_gateway ? module.vpc.private_subnets[0] : module.vpc.public_subnets[0]
      ]

      block_device_mappings = {
        root = {
          device_name = "/dev/xvda"
          ebs = {
            volume_size           = var.stable_node_disk_gb
            volume_type           = "gp3"
            encrypted             = true
            delete_on_termination = true
          }
        }
      }

      labels = {
        "metabare.com/pool" = "system"
      }

      tags = merge(local.tags, {
        Component    = "system-nodes"
        CapacityType = "on-demand"
      })
    }
  }

  tags = local.tags
}
