# Shell helpers for infrastructure/documentation/FREE_TIER_CAPACITY_PROCEDURE.md
#
# Source this file once per shell, after exporting AWS_PROFILE:
#   source infrastructure/scripts/free_tier_capacity_helpers.sh
# It defines constants and functions only; nothing runs at source time.
# Works under bash and zsh. The procedure document states what each function's
# output must look like; this file states only what each one does.
#
# Development only, by construction. Every target name is a constant below, so no
# argument or environment variable can point a function at another environment.
# Every function that writes calls fts_assert_dev first. Production is only read,
# by fts_prod_snapshot, to show it was left untouched.

FTS_REGION=ap-northeast-1
FTS_ASG=development-optinist-asg
FTS_CLUSTER=development-optinist-cloud-cluster
FTS_SERVICE=development-optinist-cloud-service
FTS_TG=development-optinist-tg
FTS_LB=development-optinist-lb
FTS_FN=development-free-manager
FTS_CLEANUP_FN=development-free-cleanup
FTS_RULE=development-free-manager-schedule
FTS_NAT_NAME=development-optinist-nat-instance

FTS_PROD_ASG=subscr-optinist-asg
FTS_PROD_CLUSTER=subscr-optinist-cloud-cluster
FTS_PROD_SERVICE=subscr-optinist-cloud-service

FTS_STATE_DIR="${FTS_STATE_DIR:-${TMPDIR:-/tmp}/free-tier-capacity}"

# --- internals -----------------------------------------------------------------------

fts__aws() { aws --region "$FTS_REGION" "$@"; }

fts__ago() {
  date -u -v-"$1"M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "-$1 min" +%Y-%m-%dT%H:%M:%SZ
}

fts__state() { mkdir -p "$FTS_STATE_DIR" && chmod 700 "$FTS_STATE_DIR"; }

# Invokes the cleanup Lambda with a JSON payload and prints its response body.
# StatusCode is 200 even when the function raised, so FunctionError is checked too.
fts__cleanup_invoke() {
  local out fe
  fts__state || return 1
  out="$FTS_STATE_DIR/cleanup_$$.json"
  fe=$(fts__aws lambda invoke --function-name "$FTS_CLEANUP_FN" --payload "$1" \
    --cli-binary-format raw-in-base64-out "$out" --query 'FunctionError' --output text) || return 1
  cat "$out"; echo
  rm -f "$out"
  [ "$fe" = "None" ] || { echo "FunctionError: $fe" >&2; return 1; }
}

# --- guard ---------------------------------------------------------------------------

# Refuses unless every development target is still a development name and the free
# ASG says it is development. The names are the guard; this catches an edited file.
fts_assert_dev() {
  local n env
  [ -n "$AWS_PROFILE" ] || { echo "REFUSING: AWS_PROFILE is not exported" >&2; return 1; }
  for n in "$FTS_ASG" "$FTS_CLUSTER" "$FTS_SERVICE" "$FTS_TG" "$FTS_LB" "$FTS_FN" \
           "$FTS_CLEANUP_FN" "$FTS_RULE" "$FTS_NAT_NAME"; do
    case "$n" in
      development-*) ;;
      *) echo "REFUSING: target '$n' is not a development name" >&2; return 1 ;;
    esac
  done
  env=$(fts__aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$FTS_ASG" \
    --query 'AutoScalingGroups[0].Tags[?Key==`Environment`]|[0].Value' --output text) || return 1
  [ "$env" = "Development" ] || { echo "REFUSING: $FTS_ASG reports Environment=$env" >&2; return 1; }
}

# --- read only -----------------------------------------------------------------------

# Who the calls run as, with the account number masked, and the environment's idle threshold.
fts_whoami() {
  echo "AWS_PROFILE=$AWS_PROFILE region=$FTS_REGION state=$FTS_STATE_DIR"
  fts__aws sts get-caller-identity --query Arn --output text | sed 's/[0-9]\{12\}/<account>/'
  echo "idle threshold: $(fts_idle_minutes) min"
}

# FREE_IDLE_THRESHOLD_MINUTES as the Free Manager reads it; that key only, never the others.
# Unset means the Free Manager's own default, 10.
fts_idle_minutes() {
  local v
  v=$(fts__aws lambda get-function-configuration --function-name "$FTS_FN" \
    --query 'Environment.Variables.FREE_IDLE_THRESHOLD_MINUTES' --output text) || return 1
  case "$v" in ''|None) echo 10 ;; *) echo "$v" ;; esac
}

# Test-pool accounts the cleanup Lambda can find by name (optinist_test_user_free_%), id and
# email. Accounts named otherwise are not listed; resolve those with fts_user_id.
fts_pool_ids() {
  fts__cleanup_invoke "{\"action\":\"get_free_test_user_ids\",\"num_users\":${1:-10}}" |
    python3 -c 'import json,sys
r = json.loads(json.loads(sys.stdin.read())["body"])["result"]
for u in r.get("users", []): print(u["id"], u["email"])
print(r.get("message", ""))'
}

# One account's database id, by email (read only).
fts_user_id() {
  [ -n "$1" ] || { echo "usage: fts_user_id <email>" >&2; return 1; }
  fts__cleanup_invoke "{\"action\":\"get_user_assignment\",\"user_email\":\"$1\"}" |
    python3 -c 'import json,sys
r = json.loads(json.loads(sys.stdin.read())["body"])["result"]
print(r.get("user_id", r.get("message", r)))'
}

# The four-number chain. Returns 0 only when the group is settled: ASG desired = InService =
# every instance in the group = ECS desired = ECS running = healthy targets. An instance still
# draining stays in the group, and ECS desired stays above the ASG until it has gone; reading
# only the running count would call that settled.
fts_chain() {
  local asg tg ecs healthy desired insvc total ecs_des ecs_run
  asg=$(fts__aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$FTS_ASG" \
    --query 'AutoScalingGroups[0].[MinSize,MaxSize,DesiredCapacity,length(Instances[?LifecycleState==`InService`]),length(Instances)]' \
    --output text) || return 1
  ecs=$(fts__aws ecs describe-services --cluster "$FTS_CLUSTER" --services "$FTS_SERVICE" \
    --query 'services[0].[desiredCount,runningCount]' --output text) || return 1
  tg=$(fts__aws elbv2 describe-target-groups --names "$FTS_TG" \
    --query 'TargetGroups[0].TargetGroupArn' --output text) || return 1
  healthy=$(fts__aws elbv2 describe-target-health --target-group-arn "$tg" \
    --query 'length(TargetHealthDescriptions[?TargetHealth.State==`healthy`])' --output text) || return 1
  desired=$(echo "$asg" | awk '{print $3}'); insvc=$(echo "$asg" | awk '{print $4}')
  total=$(echo "$asg" | awk '{print $5}')
  ecs_des=$(echo "$ecs" | awk '{print $1}'); ecs_run=$(echo "$ecs" | awk '{print $2}')
  echo "$(date -u +%H:%M:%SZ) bounds(min/max/desired)=$(echo "$asg" | awk '{print $1"/"$2"/"$3}')" \
    "inservice=$insvc/$total ecs(desired/running)=$ecs_des/$ecs_run healthy=$healthy"
  if [ "$desired" = "$insvc" ] && [ "$insvc" = "$total" ] && [ "$total" = "$ecs_des" ] &&
     [ "$ecs_des" = "$ecs_run" ] && [ "$ecs_run" = "$healthy" ]; then
    echo "CHAIN OK at $desired"
  else
    echo "CHAIN NOT SETTLED"; return 1
  fi
}

# The group's instances: id, lifecycle state, template version, zone, launch time.
fts_instances() {
  fts__aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$FTS_ASG" \
    --query 'AutoScalingGroups[0].Instances[].[InstanceId,LifecycleState,LaunchTemplate.Version,AvailabilityZone]' \
    --output text
}

# The group's scaling activities, newest first.
fts_activities() {
  fts__aws autoscaling describe-scaling-activities --auto-scaling-group-name "$FTS_ASG" \
    --max-records "${1:-5}" --no-paginate --query 'Activities[].[StartTime,EndTime,StatusCode,Description]' --output text
}

# The scaling lock over the last 20 minutes. Every value 0, or none, is clear.
fts_lock() {
  fts__aws cloudwatch get-metric-statistics --namespace "OptiNiSt/FreeManager/development" \
    --metric-name ScalingInProgress --start-time "$(fts__ago 20)" --end-time "$(fts__ago 0)" \
    --period 300 --statistics Maximum --query 'Datapoints[].Maximum' --output text
}

# TargetInstances over the last 30 minutes, oldest first.
fts_target() {
  fts__aws cloudwatch get-metric-statistics --namespace "OptiNiSt/FreeUsers/development" \
    --metric-name TargetInstances --start-time "$(fts__ago 30)" --end-time "$(fts__ago 0)" \
    --period 300 --statistics Maximum --query 'sort_by(Datapoints,&Timestamp)[].[Timestamp,Maximum]' \
    --output text
}

# The alarms that hold or block scaling: the two load alarms (scale-out, and the
# Free Manager's scale-in guard) and the Free Manager's own errors alarm.
fts_alarms() {
  fts__aws cloudwatch describe-alarms --alarm-names development-optinist-cpu-high \
    development-optinist-memory-high development-free-manager-errors \
    --query 'MetricAlarms[].[AlarmName,StateValue]' --output text
}

# Active users as the Free Manager counts them, with its own idle threshold.
fts_active() {
  local m
  m=$(fts_idle_minutes) || return 1
  fts__cleanup_invoke "{\"action\":\"count_active_users\",\"threshold_minutes\":$m}"
}

# The Free Manager's decision lines from the last N minutes (default 15).
fts_log() {
  fts__aws logs filter-log-events --log-group-name "/aws/lambda/$FTS_FN" \
    --start-time "$(( ($(date +%s) - ${1:-15} * 60) * 1000 ))" \
    --filter-pattern '?"Active users" ?"Calculated desired" ?"Scaling up" ?"Scaling down" ?"scale down" ?"Scaling ASG" ?"Successfully updated ECS" ?Sync ?"Event Type" ?WARNING ?ERROR ?Error ?"Scaling lock" ?"in progress" ?"instances ready" ?"Rebalanc" ?"Wait budget"' \
    --query 'events[].[timestamp,message]' --output text |
  while IFS="$(printf '\t')" read -r ts msg; do
    case "$ts" in ''|*[!0-9]*) continue ;; esac
    printf '%s %s\n' "$(date -u -r $((ts / 1000)) +%H:%M:%SZ 2>/dev/null || date -u -d "@$((ts / 1000))" +%H:%M:%SZ)" "$msg"
  done
}

# Everything the procedure requires before a case starts, with a verdict.
fts_preflight() {
  local stops=0 v
  fts_assert_dev || return 1
  fts_whoami
  echo "--- chain";      fts_chain || { echo "STOP: chain not settled"; stops=$((stops + 1)); }
  echo "--- instances";  fts_instances
  v=$(fts__aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$FTS_ASG" \
    --query 'length(AutoScalingGroups[0].SuspendedProcesses)' --output text)
  [ "$v" = "0" ] || { echo "STOP: $v suspended process(es) on the group"; stops=$((stops + 1)); }
  v=$(fts__aws autoscaling describe-instance-refreshes --auto-scaling-group-name "$FTS_ASG" \
    --query 'length(InstanceRefreshes[?Status==`InProgress` || Status==`Pending` || Status==`Cancelling`])' --output text)
  [ "$v" = "0" ] || { echo "STOP: an instance refresh is running"; stops=$((stops + 1)); }
  echo "--- lifecycle hooks"
  fts__aws autoscaling describe-lifecycle-hooks --auto-scaling-group-name "$FTS_ASG" \
    --query 'LifecycleHooks[].LifecycleHookName' --output text
  echo "--- scaling lock (20 min)"; v=$(fts_lock); echo "${v:-no datapoints}"
  case "$v" in *1*) echo "STOP: the scaling lock is set"; stops=$((stops + 1)) ;; esac
  echo "--- alarms"; fts_alarms
  v=$(fts_alarms | grep -c 'ALARM$')
  [ "$v" = "0" ] || { echo "STOP: an alarm is in ALARM"; stops=$((stops + 1)); }
  echo "--- schedule rule"
  v=$(fts__aws events describe-rule --name "$FTS_RULE" --query State --output text); echo "$v"
  [ "$v" = "ENABLED" ] || { echo "STOP: $FTS_RULE is $v"; stops=$((stops + 1)); }
  echo "--- NAT"
  v=$(fts__aws ec2 describe-instances --filters "Name=tag:Name,Values=$FTS_NAT_NAME" \
    --query 'Reservations[].Instances[].State.Name' --output text); echo "$v"
  case "$v" in *running*) ;; *) echo "STOP: NAT is not running (outside on-hours?)"; stops=$((stops + 1)) ;; esac
  echo "--- active users"; v=$(fts_active) || stops=$((stops + 1)); echo "$v"
  echo "==="
  if [ "$stops" -eq 0 ]; then echo "PREFLIGHT OK"; else echo "PREFLIGHT: $stops STOP item(s)"; return 1; fi
}

# Production's free tier, read only, saved under a label (before / after). The fields
# are the ones this procedure could conceivably move; nothing here writes.
fts_prod_snapshot() {
  local f
  [ -n "$1" ] || { echo "usage: fts_prod_snapshot <label>" >&2; return 1; }
  fts__state || return 1
  f="$FTS_STATE_DIR/prod-$1.txt"
  {
    fts__aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$FTS_PROD_ASG" \
      --query 'AutoScalingGroups[0].[MinSize,MaxSize,DesiredCapacity,length(SuspendedProcesses)]' --output text
    fts__aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$FTS_PROD_ASG" \
      --query 'AutoScalingGroups[0].Instances[].[InstanceId,LifecycleState]' --output text | sort
    fts__aws autoscaling describe-lifecycle-hooks --auto-scaling-group-name "$FTS_PROD_ASG" \
      --query 'LifecycleHooks[].LifecycleHookName' --output text | tr '\t' '\n' | sort
    fts__aws autoscaling describe-policies --auto-scaling-group-name "$FTS_PROD_ASG" \
      --query 'ScalingPolicies[].PolicyName' --output text | tr '\t' '\n' | sort
    fts__aws ecs describe-services --cluster "$FTS_PROD_CLUSTER" --services "$FTS_PROD_SERVICE" \
      --query 'services[0].desiredCount' --output text
  } > "$f" || return 1
  echo "saved $f"; cat "$f"
}

# Compares two production snapshots. Returns 1 on any difference.
fts_prod_compare() {
  if diff "$FTS_STATE_DIR/prod-$1.txt" "$FTS_STATE_DIR/prod-$2.txt"; then
    echo "PRODUCTION UNCHANGED ($1 -> $2)"
  else
    echo "PRODUCTION DIFFERS ($1 -> $2) — read the lines above before anything else"; return 1
  fi
}

# --- writes (development only) ---------------------------------------------------------

# Sets the group's min and max, never desired. Refuses a max below the current desired:
# UpdateAutoScalingGroup would then lower desired and let the group choose the instance.
fts_set_bounds() {
  local min="$1" max="$2" desired
  fts_assert_dev || return 1
  case "$min$max" in *[!0-9]*|'') echo "usage: fts_set_bounds <min> <max>" >&2; return 1 ;; esac
  [ "$min" -le "$max" ] || { echo "REFUSING: min $min > max $max" >&2; return 1; }
  desired=$(fts__aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$FTS_ASG" \
    --query 'AutoScalingGroups[0].DesiredCapacity' --output text) || return 1
  [ "$max" -ge "$desired" ] || { echo "REFUSING: max $max < current desired $desired; lower min first and let the Free Manager shrink" >&2; return 1; }
  echo "WRITE development: $FTS_ASG min=$min max=$max (desired left at $desired)"
  fts__aws autoscaling update-auto-scaling-group --auto-scaling-group-name "$FTS_ASG" \
    --min-size "$min" --max-size "$max" || return 1
  fts_chain
}

# Writes the activity map: one "<user id> <instance id>" pair per argument "uid:instance".
# The loop reads the map on every pass, so rewriting it re-pins users without a restart.
fts_map() {
  local p
  fts_assert_dev || return 1
  fts__state || return 1
  : > "$FTS_STATE_DIR/activity_map"
  for p in "$@"; do
    case "$p" in
      *:i-*) echo "${p%%:*} ${p#*:}" >> "$FTS_STATE_DIR/activity_map" ;;
      *) echo "bad pair '$p' (want <user id>:<instance id>)" >&2; return 1 ;;
    esac
  done
  cat "$FTS_STATE_DIR/activity_map"
}

# One pass: marks every mapped user active on its instance. Prints each user's result;
# anything other than "200 None" means that user has no fresh row.
fts_touch() {
  local u i r line=""
  fts_assert_dev || return 1
  while read -r u i; do
    [ -n "$u" ] || continue
    r=$(fts__aws lambda invoke --function-name "$FTS_CLEANUP_FN" \
      --payload "{\"action\":\"simulate_user_activity\",\"user_id\":\"$u\",\"instance_id\":\"$i\",\"minutes_ago\":0}" \
      --cli-binary-format raw-in-base64-out "$FTS_STATE_DIR/touch_$u.json" \
      --query '[StatusCode,FunctionError]' --output text 2>&1 | tr '\t\n' '  ')
    line="$line u$u:${r% }"
  done < "$FTS_STATE_DIR/activity_map"
  echo "$(date -u +%H:%M:%SZ)$line"
}

# Repeats fts_touch every INTERVAL seconds (default 120) for PASSES passes (default 45),
# or until fts_activity_stop. Meant for its own terminal or a background job.
fts_activity_loop() {
  local n=0
  fts_assert_dev || return 1
  rm -f "$FTS_STATE_DIR/activity_stop"
  while [ "$n" -lt "${1:-45}" ] && [ ! -e "$FTS_STATE_DIR/activity_stop" ]; do
    n=$((n + 1)); printf 'pass %s ' "$n"; fts_touch
    sleep "${2:-120}"
  done
  echo "activity loop ended after $n pass(es)"
}

fts_activity_stop() { fts__state && touch "$FTS_STATE_DIR/activity_stop" && echo "stop requested"; }

# Deletes every test-pool assignment row (the cleanup Lambda's own selection).
fts_cleanup_users() {
  fts_assert_dev || return 1
  echo "WRITE development: cleanup_all_test_users"
  fts__cleanup_invoke '{"action":"cleanup_all_test_users"}'
}

# --- sessions (PP-4, RS-2) -------------------------------------------------------------

# The free tier's origin. Where port 80 only redirects, the target port is the other
# listener: a request through the redirect changes origin and loses its Authorization header.
fts_base_url() {
  local arn dns port
  arn=$(fts__aws elbv2 describe-load-balancers --names "$FTS_LB" \
    --query 'LoadBalancers[0].LoadBalancerArn' --output text) || return 1
  dns=$(fts__aws elbv2 describe-load-balancers --names "$FTS_LB" \
    --query 'LoadBalancers[0].DNSName' --output text) || return 1
  port=$(fts__aws elbv2 describe-listeners --load-balancer-arn "$arn" \
    --query 'Listeners[?Protocol==`HTTP` && Port!=`80`].Port | [0]' --output text) || return 1
  case "$port" in ''|None) port=80 ;; esac
  echo "http://$dns:$port"
}

# Logs in one pool account and keeps its token in a 0600 file. Run by a person: the
# password is read without echo and passed to curl on stdin, never on a command line.
fts_login() {
  local email="$1" pw base
  fts_assert_dev || return 1
  [ -n "$email" ] || { echo "usage: fts_login <pool account email>" >&2; return 1; }
  fts__state || return 1
  base=$(fts_base_url) || return 1
  printf 'password for %s: ' "$email" >&2
  stty -echo; IFS= read -r pw; stty echo; echo >&2
  ( umask 077
    printf '%s\n%s' "$email" "$pw" |
      python3 -c 'import json,sys; e,p=sys.stdin.read().split("\n",1); print(json.dumps({"email":e,"password":p}))' |
      curl -s -X POST "$base/auth/login" -H 'Content-Type: application/json' --data-binary @- |
      python3 -c 'import json,sys; t=json.load(sys.stdin).get("access_token",""); print("Authorization: Bearer "+t if t else "")' \
      > "$FTS_STATE_DIR/auth_header" )
  pw=""
  if [ -s "$FTS_STATE_DIR/auth_header" ]; then echo "logged in; token kept in $FTS_STATE_DIR/auth_header"
  else rm -f "$FTS_STATE_DIR/auth_header"; echo "login failed" >&2; return 1; fi
}

# N requests with no cookie jar, so each arrives as a new session. Counts the serving
# instance per request (x-served-by-instance) and the HTTP status.
fts_sessions() {
  local base i
  [ -s "$FTS_STATE_DIR/auth_header" ] || { echo "no token: run fts_login first" >&2; return 1; }
  base=$(fts_base_url) || return 1
  for i in $(seq 1 "${1:-20}"); do
    curl -s -o /dev/null -D - -H @"$FTS_STATE_DIR/auth_header" "$base/workspaces" |
      awk 'NR==1{s=$2} tolower($1)=="x-served-by-instance:"{h=$2} END{gsub(/\r/,"",h); print s, (h==""?"<no header>":h)}'
  done | sort | uniq -c
}

# N requests holding one cookie jar: a session in progress must stay on one instance.
fts_sticky() {
  local base jar i
  [ -s "$FTS_STATE_DIR/auth_header" ] || { echo "no token: run fts_login first" >&2; return 1; }
  base=$(fts_base_url) || return 1
  jar="$FTS_STATE_DIR/jar"; rm -f "$jar"
  for i in $(seq 1 "${1:-5}"); do
    curl -s -o /dev/null -D - -c "$jar" -b "$jar" -H @"$FTS_STATE_DIR/auth_header" "$base/workspaces" |
      awk 'tolower($1)=="x-served-by-instance:"{gsub(/\r/,"",$2); print $2}'
  done
  rm -f "$jar"
}

fts_forget_token() { rm -f "$FTS_STATE_DIR/auth_header" "$FTS_STATE_DIR/jar"; echo "token removed"; }
