#!/usr/bin/env bash
# Kubernetes bootstrap for the micro-test AWS lab (after CloudFormation 00-07).
# Idempotent: re-run after changing manifests, knobs or the image.
#
#   IMAGE=<account>.dkr.ecr.us-east-1.amazonaws.com/<repo>:<tag> aws/kubernetes/bootstrap.sh
#
# Reads every value from CloudFormation outputs/exports. Secrets (DB password, workflow key)
# are fetched from AWS at run time and piped to kubectl; nothing secret is written to disk.
set -euo pipefail
cd "$(dirname "$0")"

: "${IMAGE:?Set IMAGE to the arm64 image URI (ECR)}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
CLUSTER=micro-test

# Pinned chart versions. Verify support for the cluster's Kubernetes version before changing.
LBC_CHART_VERSION="${LBC_CHART_VERSION:-3.6.3}"     # eks/aws-load-balancer-controller
KARPENTER_VERSION="${KARPENTER_VERSION:-1.15.2}"      # oci://public.ecr.aws/karpenter/karpenter
ISTIO_VERSION="${ISTIO_VERSION:-1.30.8}"             # istio/base, istio/istiod, istio/gateway
KEDA_VERSION="${KEDA_VERSION:-2.21.1}"               # kedacore/keda

# Lab knobs (see README "Perillas del laboratorio").
export KARPENTER_CPU_LIMIT="${KARPENTER_CPU_LIMIT:-16}"
# Concurrent steps one api pod serves well (~ Hikari pool of 10, minus headroom).
export API_MESSAGES_PER_POD="${API_MESSAGES_PER_POD:-8}"
export MOCKS_FAILURE_PROBABILITY="${MOCKS_FAILURE_PROBABILITY:-0.02}"
export MOCKS_LATENCY_MS="${MOCKS_LATENCY_MS:-0}"

step() { printf '\n==> %s\n' "$*"; }
out() { aws cloudformation describe-stacks --stack-name "$1" \
          --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text; }
imp() { aws cloudformation list-exports --query "Exports[?Name=='$1'].Value" --output text; }
need() { [[ -n "${!1}" && "${!1}" != None ]] || { echo "Missing $1 (deploy its stack first)" >&2; exit 1; }; }
# Strict ${VAR} substitution: fails on any unset variable instead of rendering blanks.
render() { python3 -c 'import os,string,sys; sys.stdout.write(string.Template(sys.stdin.read()).substitute(os.environ))' < "$1"; }

step "Reading CloudFormation outputs"
export NODE_ROLE_NAME; NODE_ROLE_NAME=$(out micro-test-eks NodeRoleName)
export CLUSTER_SG_ID; CLUSTER_SG_ID=$(imp micro-test-eks-ClusterSecurityGroupId)
export APP_SG_ID; APP_SG_ID=$(imp micro-test-network-AppSecurityGroupId)
export WORKLOAD_SUBNET_ID; WORKLOAD_SUBNET_ID=$(imp micro-test-network-AppSubnetIds | cut -d, -f1)
export TARGET_GROUP_ARN; TARGET_GROUP_ARN=$(imp micro-test-ingress-IstioTargetGroupArn)
export QUEUE_URL; QUEUE_URL=$(imp micro-test-messaging-QueueUrl)
export DLQ_URL; DLQ_URL=$(imp micro-test-messaging-DeadLetterQueueUrl)
export DOCUMENTS_BUCKET; DOCUMENTS_BUCKET=$(imp micro-test-data-DocumentsBucketName)
VPC_ID=$(imp micro-test-network-VpcId)
INTERRUPTION_QUEUE=$(out micro-test-eks KarpenterInterruptionQueueName)
DB_SECRET_ARN=$(imp micro-test-data-DbSecretArn)
for v in NODE_ROLE_NAME CLUSTER_SG_ID APP_SG_ID WORKLOAD_SUBNET_ID TARGET_GROUP_ARN QUEUE_URL DLQ_URL \
         DOCUMENTS_BUCKET VPC_ID INTERRUPTION_QUEUE DB_SECRET_ARN; do need "$v"; done

# Sync rule (aws/diagrams/ESCALADO.md): api pods never exceed what admitted executions can use.
# MaxConcurrency (04) = busy Lambdas = running executions = concurrent API steps.
export MAX_CONCURRENCY; MAX_CONCURRENCY=$(aws cloudformation describe-stacks --stack-name micro-test-messaging \
  --query "Stacks[0].Parameters[?ParameterKey=='MaxConcurrency'].ParameterValue" --output text)
need MAX_CONCURRENCY
export API_MAX_REPLICAS="${API_MAX_REPLICAS:-$(( (MAX_CONCURRENCY + API_MESSAGES_PER_POD - 1) / API_MESSAGES_PER_POD ))}"
echo "MaxConcurrency=$MAX_CONCURRENCY -> api max replicas=$API_MAX_REPLICAS ($API_MESSAGES_PER_POD steps/pod)"

# Isolated kubeconfig: never touches the LocalStack context in ~/.kube/config.
export KUBECONFIG; KUBECONFIG="$(mktemp -d)/config"
aws eks update-kubeconfig --name "$CLUSTER" --region "$AWS_REGION" >/dev/null
kubectl get nodes -l role=base

step "Helm repositories"
helm repo add eks https://aws.github.io/eks-charts --force-update >/dev/null
helm repo add istio https://istio-release.storage.googleapis.com/charts --force-update >/dev/null
helm repo add kedacore https://kedacore.github.io/charts --force-update >/dev/null
helm repo update eks istio kedacore >/dev/null

step "AWS Load Balancer Controller $LBC_CHART_VERSION (TargetGroupBinding only)"
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system --version "$LBC_CHART_VERSION" --wait \
  --set clusterName="$CLUSTER" --set region="$AWS_REGION" --set vpcId="$VPC_ID" \
  --set serviceAccount.name=aws-load-balancer-controller \
  --set replicaCount=1 --set nodeSelector.role=base \
  --set enableServiceMutatorWebhook=false   # Services never create load balancers; CFN owns the NLB

step "Karpenter $KARPENTER_VERSION (controller on base nodes)"
helm upgrade --install karpenter oci://public.ecr.aws/karpenter/karpenter \
  -n kube-system --version "$KARPENTER_VERSION" --wait \
  --set settings.clusterName="$CLUSTER" --set settings.interruptionQueue="$INTERRUPTION_QUEUE" \
  --set replicas=1 --set nodeSelector.role=base \
  --set controller.resources.requests.cpu=100m --set controller.resources.requests.memory=256Mi \
  --set controller.resources.limits.memory=512Mi

step "Istio $ISTIO_VERSION"
helm upgrade --install istio-base istio/base -n istio-system --create-namespace \
  --version "$ISTIO_VERSION" --wait
helm upgrade --install istiod istio/istiod -n istio-system --version "$ISTIO_VERSION" --wait \
  --set pilot.nodeSelector.role=base --set pilot.autoscaleEnabled=false \
  --set pilot.resources.requests.cpu=100m --set pilot.resources.requests.memory=256Mi
kubectl create namespace istio-ingress --dry-run=client -o yaml | kubectl apply -f -
# ClusterIP only: the NLB reaches pod IPs through the TargetGroupBinding (port 80 -> pod 8080).
helm upgrade --install istio-ingressgateway istio/gateway -n istio-ingress \
  --version "$ISTIO_VERSION" --wait -f - <<'VALUES'
replicaCount: 1
autoscaling: { enabled: false }
nodeSelector: { role: base }
resources: { requests: { cpu: 100m, memory: 128Mi } }
service:
  type: ClusterIP
  ports:
    - { name: status-port, port: 15021, targetPort: 15021, protocol: TCP }
    - { name: http2, port: 80, targetPort: 8080, protocol: TCP }
VALUES

step "KEDA $KEDA_VERSION"
helm upgrade --install keda kedacore/keda -n keda --create-namespace \
  --version "$KEDA_VERSION" --wait --set nodeSelector.role=base

step "Platform objects: Karpenter NodePool, mTLS STRICT, Gateway, TargetGroupBinding"
render platform.yaml | kubectl apply -f -

step "Application secrets (from Secrets Manager, never on disk)"
kubectl create namespace micro --dry-run=client -o yaml | kubectl apply -f -
read -r DB_HOST DB_PORT DB_USER DB_PASS < <(
  aws secretsmanager get-secret-value --secret-id "$DB_SECRET_ARN" --query SecretString --output text |
  python3 -c 'import json,sys; s=json.load(sys.stdin); print(s["host"], s["port"], s["username"], s["password"])')
kubectl -n micro create secret generic db --dry-run=client -o yaml --from-env-file=<(printf '%s\n' \
  "DATABASE_URL=jdbc:postgresql://${DB_HOST}:${DB_PORT}/docs?sslmode=require" \
  "DATABASE_USERNAME=${DB_USER}" "DATABASE_PASSWORD=${DB_PASS}") | kubectl apply -f -
unset DB_PASS
CONNECTION_SECRET=$(aws events describe-connection --name micro-test-api-internal --query SecretArn --output text)
export WORKFLOW_KEY; WORKFLOW_KEY=$(
  aws secretsmanager get-secret-value --secret-id "$CONNECTION_SECRET" --query SecretString --output text |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["api_key_value"])')

step "Application: api, worker, mocks, routing, authorization, KEDA"
render app.yaml | kubectl apply -f -
unset WORKFLOW_KEY
kubectl -n micro rollout restart deploy/api deploy/worker deploy/mocks >/dev/null  # pick up secret/config changes
for d in api worker mocks; do kubectl -n micro rollout status "deploy/$d" --timeout=15m; done

step "Checks"
kubectl get nodes -L role,karpenter.sh/nodepool,karpenter.sh/capacity-type,node.kubernetes.io/instance-type
kubectl -n istio-ingress get targetgroupbinding
aws elbv2 describe-target-health --target-group-arn "$TARGET_GROUP_ARN" \
  --query "TargetHealthDescriptions[].[Target.Id,TargetHealth.State]" --output text
kubectl -n micro get pods,scaledobject
echo "Done. kubectl access: aws eks update-kubeconfig --name $CLUSTER --alias micro-test-aws"
