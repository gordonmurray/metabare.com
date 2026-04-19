#!/bin/bash
# Metabare instance bootstrap. Installs Docker + Compose, pulls credentials
# from Secrets Manager, clones the repo, and brings up the prod compose
# stack. Runs once at first boot; replacement on user_data change is
# enabled in compute.tf.
set -Eeuo pipefail
exec > >(tee /var/log/metabare-bootstrap.log | logger -t user-data -s 2>/dev/console) 2>&1

echo "[$(date)] metabare bootstrap starting"

dnf install -y docker git jq
systemctl enable --now docker

# Compose plugin (the AL2023 docker package ships without it)
COMPOSE_VERSION=v2.29.7
DOCKER_PLUGINS=/usr/local/lib/docker/cli-plugins
mkdir -p "$DOCKER_PLUGINS"
curl -sL "https://github.com/docker/compose/releases/download/$${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
    -o "$DOCKER_PLUGINS/docker-compose"
chmod +x "$DOCKER_PLUGINS/docker-compose"

# Resolve app credentials from Secrets Manager into /opt/metabare/.env
SECRET_JSON=$(aws --region ${region} secretsmanager get-secret-value \
    --secret-id ${secret_id} --query SecretString --output text)

mkdir -p /opt/metabare
cat > /opt/metabare/.env <<EOF
S3_ACCESS_KEY=$(echo "$SECRET_JSON" | jq -r .AWS_ACCESS_KEY_ID)
S3_SECRET_KEY=$(echo "$SECRET_JSON" | jq -r .AWS_SECRET_ACCESS_KEY)
S3_BUCKET=${bucket}
S3_REGION=${region}
BASE_IMAGE_URL=https://${cdn_domain}/
SEARCH_BACKEND=firn
FIRN_NAMESPACE=${firn_namespace}
EOF
chmod 600 /opt/metabare/.env

# Clone the application source
cd /opt/metabare
if [ ! -d repo/.git ]; then
    git clone -b ${git_branch} ${git_repo} repo
fi
cd repo

# Bring up the prod compose stack. First boot pulls + builds, ~10 min.
docker compose -f docker-compose.prod.yml --env-file /opt/metabare/.env up -d --build

echo "[$(date)] metabare bootstrap done"
