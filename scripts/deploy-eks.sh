#!/usr/bin/env bash
# Deploy MetaBare to the EKS environment.
#
# Reads the values it needs from Terraform outputs rather than taking them as
# arguments, so the manifests cannot drift from the infrastructure they run on.
# Everything else is plain kubectl against manifests in deploy/components/.
#
#   ./scripts/deploy-eks.sh [dev]
#
# Requires: terraform, kubectl, aws, docker.

set -euo pipefail

ENVIRONMENT="${1:-dev}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="${REPO_ROOT}/infra/environments/${ENVIRONMENT}"
MANIFESTS="${REPO_ROOT}/deploy/components"

fail() { echo "FAIL: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

for tool in terraform kubectl aws docker; do
    command -v "${tool}" >/dev/null 2>&1 || fail "${tool} is required but not installed"
done

[ -d "${TF_DIR}" ] || fail "no such environment: ${TF_DIR}"

step "Reading Terraform outputs"
tf() { terraform -chdir="${TF_DIR}" output -raw "$1"; }

CLUSTER_NAME="$(tf cluster_name)"
REGION="$(tf region)"
DATA_BUCKET="$(tf data_bucket)"
CPU_QUEUE_URL="$(tf cpu_queue_url)"
GPU_QUEUE_URL="$(tf gpu_queue_url)"
ECR_URI="$(tf ecr_repository_url)"

# Terraform reaches AWS through a profile set in providers.tf. The AWS CLI does
# not inherit that, so without this every aws command below would use whatever
# the ambient default profile happens to be. That is not a style issue: it
# would push the image to a different account's ECR, point kubectl at a
# different cluster, and do it silently if a same-named cluster exists there.
AWS_PROFILE_FROM_TF="$(terraform -chdir="${TF_DIR}" output -raw aws_profile 2>/dev/null || true)"
if [ -n "${AWS_PROFILE_FROM_TF}" ]; then
    export AWS_PROFILE="${AWS_PROFILE_FROM_TF}"
fi
export AWS_DEFAULT_REGION="${REGION}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
EXPECTED_ACCOUNT_ID="$(tf account_id)"
if [ "${ACCOUNT_ID}" != "${EXPECTED_ACCOUNT_ID}" ]; then
    fail "credentials resolve to account ${ACCOUNT_ID}, but this environment was applied into ${EXPECTED_ACCOUNT_ID}. Refusing to deploy."
fi

echo "    account: ${ACCOUNT_ID}"
echo "    cluster: ${CLUSTER_NAME}"
echo "    region:  ${REGION}"
echo "    bucket:  ${DATA_BUCKET}"

step "Configuring kubectl"
aws eks update-kubeconfig --name "${CLUSTER_NAME}" --region "${REGION}" >/dev/null
kubectl config current-context

step "Building and pushing the API image"
# The repository and its lifecycle policy are Terraform-managed, so they are
# removed by `make destroy` and old images are expired. A repository created
# here as a side effect would survive teardown and keep billing.
ECR_REPO="${ECR_URI#*.amazonaws.com/}"

aws ecr get-login-password --region "${REGION}" \
    | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null

# The tag must identify the bytes that were built, because ECR tags are
# immutable and a benchmark or a rollback refers to a tag.
#
# A short SHA alone is not enough: `git diff --quiet` ignores staged and
# untracked files, so two different working trees can produce the same tag and
# the second push fails, or worse, is skipped and the wrong image is deployed.
# So a dirty tree is refused by default, and when explicitly allowed the tag
# carries a digest of the actual differences.
COMMIT_SHA="$(git -C "${REPO_ROOT}" rev-parse --short HEAD)"
TREE_STATUS="$(git -C "${REPO_ROOT}" status --porcelain)"
if [ -z "${TREE_STATUS}" ]; then
    IMAGE_TAG="${COMMIT_SHA}"
elif [ "${ALLOW_DIRTY:-0}" = "1" ]; then
    DIRTY_DIGEST="$(
        {
            git -C "${REPO_ROOT}" diff HEAD
            git -C "${REPO_ROOT}" ls-files --others --exclude-standard -z \
                | xargs -0 -r sha256sum
        } | sha256sum | cut -c1-12
    )"
    IMAGE_TAG="${COMMIT_SHA}-dirty-${DIRTY_DIGEST}"
    echo "    WARNING: deploying a dirty working tree as ${IMAGE_TAG}"
else
    fail "working tree is dirty. Commit first, or set ALLOW_DIRTY=1 to deploy a content-hashed dirty build."
fi
# Tags are immutable, so pushing one that already exists is an error rather
# than a no-op. Re-running a deploy for the same commit is a normal thing to
# do (a rollout failed, a manifest changed, someone is redeploying), so the
# existing image is reused instead of rebuilt.
if aws ecr describe-images \
        --repository-name "${ECR_REPO}" \
        --image-ids imageTag="${IMAGE_TAG}" \
        --region "${REGION}" >/dev/null 2>&1; then
    echo "    ${IMAGE_TAG} is already in ECR, reusing it"
else
    docker build \
        --platform linux/amd64 \
        -f "${REPO_ROOT}/services/api/Dockerfile" \
        -t "${ECR_URI}:${IMAGE_TAG}" \
        "${REPO_ROOT}"
    docker push "${ECR_URI}:${IMAGE_TAG}"
fi

IMAGE_DIGEST="$(aws ecr describe-images \
    --repository-name "${ECR_REPO}" \
    --image-ids imageTag="${IMAGE_TAG}" \
    --region "${REGION}" \
    --query 'imageDetails[0].imageDigest' --output text)"
IMAGE_REF="${ECR_URI}@${IMAGE_DIGEST}"
echo "    ${IMAGE_REF}"

step "Applying namespace"
kubectl apply -f "${MANIFESTS}/namespace.yaml"

step "Applying configuration"
kubectl -n metabare create configmap metabare-config \
    --from-literal=METABARE_ENVIRONMENT="${ENVIRONMENT}" \
    --from-literal=METABARE_BUCKET="${DATA_BUCKET}" \
    --from-literal=METABARE_REGION="${REGION}" \
    --from-literal=METABARE_CPU_QUEUE_URL="${CPU_QUEUE_URL}" \
    --from-literal=METABARE_GPU_QUEUE_URL="${GPU_QUEUE_URL}" \
    --from-literal=data_bucket="${DATA_BUCKET}" \
    --from-literal=region="${REGION}" \
    --dry-run=client -o yaml | kubectl apply -f -

# Firn's API keys. Generated here and stored only in the cluster: they are not
# AWS credentials, so a Kubernetes Secret is the right home for them, unlike
# static AWS keys, which belong nowhere.
#
# Created only if absent, so re-running this script does not rotate the keys
# out from under a running Firn.
if ! kubectl -n metabare get secret firn-auth >/dev/null 2>&1; then
    step "Generating Firn API keys"
    kubectl -n metabare create secret generic firn-auth \
        --from-literal=api-key="$(openssl rand -hex 32)" \
        --from-literal=admin-api-key="$(openssl rand -hex 32)"
else
    echo "    firn-auth secret already exists, leaving it alone"
fi

step "Deploying Firn"
kubectl apply -f "${MANIFESTS}/firn.yaml"
kubectl -n metabare rollout status deployment/firn --timeout=5m

step "Deploying the API"
sed "s|METABARE_API_IMAGE|${IMAGE_REF}|" "${MANIFESTS}/api.yaml" | kubectl apply -f -
kubectl -n metabare rollout status deployment/api --timeout=10m

step "Deployed"
kubectl -n metabare get pods -o wide
echo
echo "Verify with:"
echo "  kubectl -n metabare port-forward svc/api 8080:8080 &"
echo "  API_URL=http://localhost:8080 ./scripts/smoke.sh"
