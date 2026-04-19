# infra

Single-instance Metabare deployment in `eu-west-1` plus a CloudFront
distribution for image delivery. The state bucket
`metabare-tfstate-358b07cf` was created out-of-band; everything else
lives here.

## Layout

```
versions.tf      # terraform 1.10.5, AWS provider ~> 5.0
backend.tf       # S3 backend with native locking (use_lockfile)
providers.tf     # eu-west-1 default, us-east-1 alias for the CloudFront cert
variables.tf
data.tf          # default VPC + subnets, AL2023 AMI, common tags
certs.tf         # ACM certs (ALB + CloudFront)
network.tf       # ALB and instance security groups
storage.tf       # S3 bucket for Lance data and image binaries
iam.tf           # app IAM user, app-creds secret, instance role + profile
compute.tf       # EC2 instance with templated user-data
alb.tf           # ALB, target group, listeners
cloudfront.tf    # OAC, distribution, bucket policy granting CloudFront read
outputs.tf
user-data.sh.tpl # EC2 first-boot script
```

## Apply procedure

The DNS provider for `metabare.com` is Namecheap, so ACM validation
records have to be added by hand. Run apply in two stages:

```bash
cd infra
terraform init
terraform plan -var "git_repo=https://github.com/<owner>/metabare.com.git"

# Stage 1: only request the certs.
terraform apply -var "git_repo=..." \
    -target=aws_acm_certificate.alb \
    -target=aws_acm_certificate.cdn

# Show the CNAMEs to copy into Namecheap.
terraform output acm_validation_records_alb
terraform output acm_validation_records_cdn
```

Add those CNAMEs in Namecheap. ACM polls every minute or so; once
validated (usually 5-10 min after the records propagate), continue:

```bash
# Stage 2: everything else. Validation resources will poll until
# the certs are issued, then the rest of the stack rolls out.
terraform apply -var "git_repo=..."
```

Final step is the public DNS cutover:

- `metabare.com` and `www.metabare.com` -> CNAME -> output
  `alb_dns_name`. (Namecheap requires CNAMEs at subdomains; for the
  apex use ALIAS-equivalent record.)
- `cdn.metabare.com` -> CNAME -> output `cloudfront_domain`.

## Verifying the box came up

```bash
# Open a shell over SSM (no SSH port is open).
aws --profile cloudfloe ssm start-session \
    --target $(terraform output -raw instance_id)

# Inside:
sudo cat /var/log/metabare-bootstrap.log
sudo docker compose -f /opt/metabare/repo/docker-compose.prod.yml ps
```

## Tearing down

```bash
terraform destroy -var "git_repo=..."
```

The state bucket is not managed here; remove it manually if you no
longer want it:

```bash
aws --profile cloudfloe s3 rm s3://metabare-tfstate-358b07cf --recursive
aws --profile cloudfloe s3 rb s3://metabare-tfstate-358b07cf
```
