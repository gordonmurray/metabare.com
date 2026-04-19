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
data.tf          # caller identity, AL2023 AMI, common tags
locals.tf-style locals are inlined in data.tf for now
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

The hosted zone for `metabare.com` is in Route 53 (Namecheap is now
just the registrar pointing at AWS NS), so DNS records and ACM
validation are fully automated. One apply does the whole thing:

```bash
cd infra
terraform init
terraform plan -var "git_repo=https://github.com/gordonmurray/metabare.com.git"
terraform apply -var "git_repo=https://github.com/gordonmurray/metabare.com.git"
```

The apply creates the cert validation records, waits for ACM to see
them and issue the certs (usually a few minutes once the registrar's
NS change has propagated), then rolls out the ALB, EC2, CloudFront,
and the apex / www / cdn DNS records.

Total wall clock on a fresh apply is about 20 minutes; CloudFront
is the slow piece (initial propagation is ~15 min).

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
terraform destroy -var "git_repo=https://github.com/gordonmurray/metabare.com.git"
```

The state bucket is not managed here; remove it manually if you no
longer want it:

```bash
aws --profile cloudfloe s3 rm s3://metabare-tfstate-358b07cf --recursive
aws --profile cloudfloe s3 rb s3://metabare-tfstate-358b07cf
```
