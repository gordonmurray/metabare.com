#!/bin/bash
# Metabare instance bootstrap. Installs Docker + Compose, clones
# the repo, writes a small env file, and brings up the prod
# compose stack. S3 credentials come from the instance profile
# via IMDS, so no secret fetch happens here.
set -Eeuo pipefail
exec > >(tee /var/log/metabare-bootstrap.log | logger -t user-data -s 2>/dev/console) 2>&1

echo "[$(date)] metabare bootstrap starting"

dnf install -y docker git jq
systemctl enable --now docker

# Compose plugin (AL2023 docker package ships without it)
COMPOSE_VERSION=v2.29.7
DOCKER_PLUGINS=/usr/local/lib/docker/cli-plugins
mkdir -p "$DOCKER_PLUGINS"
curl -sL "https://github.com/docker/compose/releases/download/$${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
    -o "$DOCKER_PLUGINS/docker-compose"
chmod +x "$DOCKER_PLUGINS/docker-compose"

mkdir -p /opt/metabare
cat > /opt/metabare/.env <<EOF
S3_BUCKET=${bucket}
S3_REGION=${region}
BASE_IMAGE_URL=https://${cdn_domain}/
SEARCH_BACKEND=firn
FIRN_NAMESPACE=${firn_namespace}
# Firn bearer-token auth (v0.5.0+). Empty values leave the API open
# with a startup WARN, which is the current posture: Firn is only
# reachable from inside the compose network. Set both to lock the
# API down (will need to be matched on upload + search callers in a
# future compose iteration).
FIRNFLOW_API_KEY=
FIRNFLOW_ADMIN_API_KEY=
EOF
chmod 600 /opt/metabare/.env

cd /opt/metabare
if [ ! -d repo/.git ]; then
    git clone -b ${git_branch} ${git_repo} repo
fi
cd repo

docker compose -f docker-compose.prod.yml --env-file /opt/metabare/.env up -d --build

echo "[$(date)] metabare bootstrap done"
