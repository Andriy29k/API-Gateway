#!/usr/bin/env bash
# Demo helper for the API Gateway (T-026). Each step prints and runs a few commands.
# The full scenario with talking points is in docs/demo.md.
#
# Usage:  bash scripts/demo.sh <step>
# Steps:  routing | ratelimit | failure | faults | load | status | all
#
# Environment:
#   GATEWAY_URL  gateway base URL       (default https://api.api-gateway.pp.ua)
#   VM           ssh target of the VM   (default azureuser@<terraform output server_public_ip>)
#   NO_PAUSE=1   do not wait for Enter between commands (rehearsal)
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-https://api.api-gateway.pp.ua}"
GATEWAY_URL="${GATEWAY_URL%/}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

vm() {
  if [ -z "${VM:-}" ]; then
    VM="azureuser@$(terraform -chdir="$REPO_ROOT/terraform" output -raw server_public_ip)"
  fi
  echo "$VM"
}

# kubectl on the k3s server (no local kubectl needed), as text for run()
kc() { echo "ssh $(vm) sudo k3s kubectl"; }

title() { printf '\n\033[1;34m== %s ==\033[0m\n' "$1"; }

# Print a command, wait for Enter (unless NO_PAUSE=1), then run it.
run() {
  printf '\n\033[1;32m$ %s\033[0m\n' "$*"
  if [ "${NO_PAUSE:-0}" != 1 ]; then read -r -p "(Enter to run)" _; fi
  bash -c "$*" || true
}

step_routing() {
  title "Routing, request ID, JSON errors"
  run "curl -s $GATEWAY_URL/health; echo"
  run "curl -s -i '$GATEWAY_URL/api/service-a/hello?name=demo' | grep -iE '^HTTP|^x-request-id|^\{'"
  run "curl -s '$GATEWAY_URL/api/service-c/items?limit=2'; echo"
  run "curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}\n' '$GATEWAY_URL/api/service-a?x=1'"
  run "curl -s -w '  (%{http_code})\n' $GATEWAY_URL/unknown"
  run "curl -s -w '  (%{http_code})\n' -X POST $GATEWAY_URL/api/service-b/echo -d 'not json'"
}

step_ratelimit() {
  title "Rate limiting: 30 requests at the same moment (limit 5 r/s, burst 10)"
  run "seq 1 30 | xargs -P 30 -I{} curl -s -o /dev/null -w '%{http_code}\n' $GATEWAY_URL/api/service-a/hello | sort | uniq -c"
  # One rejected response in detail: save every response of a new burst, show the first 429.
  run "sleep 3; d=\$(mktemp -d); seq 1 30 | xargs -P 30 -I{} curl -s -i -o \$d/{} $GATEWAY_URL/api/service-a/hello; grep -l '^HTTP/[0-9.]* 429' \$d/* | head -1 | xargs grep -iE '^HTTP|^retry-after|^x-request-id|^\{'; echo; rm -rf \$d"
}

step_failure() {
  title "Backend failure and self-heal: service-a is stopped, Argo CD brings it back"
  run "$(kc) -n api-gateway get deploy service-a"
  run "$(kc) -n api-gateway scale deploy/service-a --replicas=0"
  run "s=\$(date +%s); for i in \$(seq 1 40); do c=\$(curl -s -m 5 -o /tmp/demo-body -w '%{http_code}' $GATEWAY_URL/api/service-a/hello); echo \"+\$(( \$(date +%s)-s ))s \$c \$(head -c 70 /tmp/demo-body)\"; [ \"\$c\" = 200 ] && break; sleep 1; done; rm -f /tmp/demo-body"
  run "$(kc) -n api-gateway get deploy service-a"
}

step_faults() {
  title "Fault injection with service-d (enabled only for the demo)"
  # Append the parameter (JSON patch): replacing the list would also drop the image.tag parameter.
  run "ssh $(vm) \"sudo k3s kubectl -n argocd patch application api-gateway --type json -p '[{\\\"op\\\":\\\"add\\\",\\\"path\\\":\\\"/spec/source/helm/parameters/-\\\",\\\"value\\\":{\\\"name\\\":\\\"services.service-d.enabled\\\",\\\"value\\\":\\\"true\\\"}}]'\""
  run "for i in \$(seq 1 40); do [ \"\$(curl -s -o /dev/null -w '%{http_code}' $GATEWAY_URL/api/service-d/hello)\" = 200 ] && break; sleep 3; done; echo service-d is up"
  run "curl -s -w '  (%{http_code})\n' '$GATEWAY_URL/api/service-d/error?status=503'"
  run "time curl -s -w '  (%{http_code})\n' '$GATEWAY_URL/api/service-d/slow?seconds=12'"
  run "ssh $(vm) sudo k3s kubectl apply -f - < $REPO_ROOT/k8s/argocd/api-gateway.yaml"
  run "for i in \$(seq 1 40); do [ \"\$(curl -s -o /dev/null -w '%{http_code}' $GATEWAY_URL/api/service-d/hello)\" = 404 ] && break; sleep 3; done; echo service-d is removed again"
}

step_load() {
  title "Load test (watch Grafana: 429 responses/s, response time)"
  run "GATEWAY_URL=$GATEWAY_URL k6 run --quiet $REPO_ROOT/load/rate-limit.js"
}

step_status() {
  title "Cluster and GitOps status"
  run "$(kc) -n argocd get applications"
  run "$(kc) -n api-gateway get pods"
  run "$(kc) -n monitoring get pods"
}

case "${1:-}" in
  routing) step_routing ;;
  ratelimit) step_ratelimit ;;
  failure) step_failure ;;
  faults) step_faults ;;
  load) step_load ;;
  status) step_status ;;
  all) step_status; step_routing; step_ratelimit; step_failure; step_faults; step_load ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
