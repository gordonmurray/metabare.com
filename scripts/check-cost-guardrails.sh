#!/usr/bin/env bash
# Cost guardrails, enforced in CI.
#
# Guards against accidental broad instance-family expansion and unlimited
# NodePools. A deliberately dumb grep-level check rather than a policy engine:
# it catches the specific mistakes that turn into a surprise bill, and it is
# obvious enough that nobody has to guess why it failed.
#
# It is not a substitute for reading a plan.

set -euo pipefail

failures=0

fail() {
    echo "FAIL: $*" >&2
    failures=$((failures + 1))
}

ok() {
    echo "  ok: $*"
}

echo "==> Karpenter NodePools declare limits"
nodepools=$(grep -rl 'kind:[[:space:]]*NodePool' deploy/ 2>/dev/null || true)
if [ -z "${nodepools}" ]; then
    ok "no NodePools yet"
else
    while IFS= read -r file; do
        if grep -q '^[[:space:]]*limits:' "${file}"; then
            ok "${file} declares limits"
        else
            fail "${file} defines a NodePool with no limits: block. An unbounded NodePool can provision unlimited capacity."
        fi
    done <<< "${nodepools}"
fi

echo "==> No always-on GPU replicas"
# Scale-to-zero has to be real. A ScaledObject with a non-zero
# minReplicaCount silently defeats the entire premise of the project.
scaledobjects=$(grep -rl 'kind:[[:space:]]*ScaledObject' deploy/ 2>/dev/null || true)
if [ -z "${scaledobjects}" ]; then
    ok "no ScaledObjects yet"
else
    while IFS= read -r file; do
        min=$(grep -E '^[[:space:]]*minReplicaCount:' "${file}" | grep -oE '[0-9]+' | head -1 || true)
        if [ -n "${min}" ] && [ "${min}" != "0" ]; then
            fail "${file} sets minReplicaCount=${min}. Scale-to-zero means zero."
        else
            ok "${file} scales to zero"
        fi
    done <<< "${scaledobjects}"
fi

echo "==> Fixed-cost resources are declared, not incidental"
# A NAT Gateway, ALB or NLB is allowed, but only where its cost has been
# accounted for. The marker comment is the acknowledgement.
#
# Scoped to tracked files: .terraform/modules/ holds vendored upstream modules
# that legitimately define these resources behind a feature flag, and this
# repository's decision is expressed by whether it *enables* them, not by
# what the module is capable of.
for resource in aws_nat_gateway aws_lb; do
    hits=$(git ls-files -z 'infra/**' | xargs -0 grep -ln "resource \"${resource}\"" 2>/dev/null || true)
    if [ -z "${hits}" ]; then
        ok "no ${resource}"
        continue
    fi
    while IFS= read -r file; do
        if grep -q 'cost-acknowledged' "${file}"; then
            ok "${file} declares ${resource} with a cost acknowledgement"
        else
            fail "${file} creates a ${resource} without a 'cost-acknowledged' comment. Update the cost table in the README first."
        fi
    done <<< "${hits}"
done

echo "==> No static AWS credentials committed"
# No static AWS credentials anywhere, including Kubernetes Secrets.
# Scoped to tracked files via git ls-files: "committed" is the actual concern,
# and scanning the working tree would trip over example keys inside vendored
# dependencies such as moto.
if git ls-files -z | xargs -0 grep -lE 'AKIA[0-9A-Z]{16}' 2>/dev/null | grep .; then
    fail "what looks like an AWS access key id is in a tracked file"
else
    ok "no AWS access key ids in tracked files"
fi

echo
if [ "${failures}" -gt 0 ]; then
    echo "${failures} guardrail failure(s)."
    exit 1
fi
echo "PASS: cost guardrails"
