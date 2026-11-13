#!/usr/bin/env bash
# READ-ONLY audit of a deployed student-hosting stack: it only runs describe/get/list/simulate calls and one anonymous curl.
# It changes nothing. It is UNTESTED against a live stack: read it first, run it on your TEST account only.
#
#   AWS_PROFILE=teacher-test bash verify-platform.sh [stack-name]
#
# Output: PASS / FAIL (a design property is violated) / WARN (a known gap or something to look at) / INFO.
set -u
STACK="${1:-student-hosting}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
pass=0; fail=0; warn=0

ok()   { printf '  PASS  %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  FAIL  %s   [%s]\n' "$1" "$2"; fail=$((fail+1)); }
note() { printf '  WARN  %s   [%s]\n' "$1" "$2"; warn=$((warn+1)); }
info() { printf '  INFO  %s\n' "$1"; }
clean(){ [ "$1" = "None" ] && echo "" || echo "$1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected '$2', got '$3'"; fi; }
out()  { clean "$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null)"; }

echo "== Who am I =="
ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)"
[ -z "$ACCOUNT" ] && { echo "Cannot call AWS (no profile or no permission). Set AWS_PROFILE."; exit 2; }
info "account $ACCOUNT, region $AWS_REGION, profile ${AWS_PROFILE:-<default>}, stack $STACK"
info "STOP if this is not your TEST account."

echo "== Stack =="
STATUS="$(clean "$(aws cloudformation describe-stacks --stack-name "$STACK" --query 'Stacks[0].StackStatus' --output text 2>/dev/null)")"
case "$STATUS" in CREATE_COMPLETE|UPDATE_COMPLETE) ok "stack status $STATUS";; "") bad "stack exists" "not found"; echo "No stack: nothing more to check."; exit 1;; *) bad "stack status healthy" "$STATUS";; esac
info "termination protection: $(aws cloudformation describe-stacks --stack-name "$STACK" --query 'Stacks[0].EnableTerminationProtection' --output text)"

echo "== S3 =="
BUCKET="$(out BucketName)"
if [ -z "$BUCKET" ]; then bad "bucket output present" "missing"; else
  bpa="$(aws s3api get-public-access-block --bucket "$BUCKET" --query 'PublicAccessBlockConfiguration.[BlockPublicAcls,BlockPublicPolicy,IgnorePublicAcls,RestrictPublicBuckets]' --output text 2>/dev/null | tr '\t' ' ')"
  check "Block Public Access: all four on" "True True True True" "$bpa"
  check "bucket versioning enabled" "Enabled" "$(clean "$(aws s3api get-bucket-versioning --bucket "$BUCKET" --query Status --output text 2>/dev/null)")"
  n="$(aws s3api get-bucket-policy --bucket "$BUCKET" --query Policy --output text 2>/dev/null | grep -o 'AWS:SourceArn' | wc -l | tr -d ' ')"
  if [ "${n:-0}" -ge 3 ]; then ok "bucket policy has AWS:SourceArn on >= 3 statements ($n)"; else bad "bucket policy confused-deputy conditions" "found $n"; fi
  enc="$(clean "$(aws s3api get-bucket-encryption --bucket "$BUCKET" --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.SSEAlgorithm' --output text 2>/dev/null)")"
  if [ -n "$enc" ]; then ok "default encryption: $enc"; else note "default encryption not reported" "SSE-S3 is applied automatically; confirm in the console"; fi
  rules="$(aws s3api get-bucket-cors --bucket "$BUCKET" --query 'length(CORSRules)' --output text 2>/dev/null)"
  check "bucket CORS has exactly 2 rules" "2" "$rules"
  code="$(curl -s -o /dev/null -w '%{http_code}' "https://$BUCKET.s3.$AWS_REGION.amazonaws.com/sites/zz-nonexistent/index.html")"
  check "direct anonymous S3 access is denied (403)" "403" "$code"
fi

echo "== CloudFront =="
DIST="$(out DistributionId)"; PORTAL="$(out PortalDistributionId)"
for d in "$DIST" "$PORTAL"; do
  [ -z "$d" ] && continue
  oac="$(aws cloudfront get-distribution-config --id "$d" --query 'DistributionConfig.Origins.Items[?S3OriginConfig!=null].OriginAccessControlId' --output text 2>/dev/null)"
  if [ -n "$oac" ] && [ "$oac" != "None" ]; then ok "distribution $d: S3 origin uses an origin access control"; else bad "distribution $d: OAC on the S3 origin" "none"; fi
  pc="$(aws cloudfront get-distribution-config --id "$d" --query 'DistributionConfig.PriceClass' --output text 2>/dev/null)"
  info "distribution $d price class $pc"
  dv="$(aws cloudfront get-distribution-config --id "$d" --query 'DistributionConfig.DefaultCacheBehavior.ViewerProtocolPolicy' --output text 2>/dev/null)"
  check "distribution $d: default behavior redirects to HTTPS" "redirect-to-https" "$dv"
done
nfun="$(aws cloudfront list-functions --query "length(FunctionList.Items[?contains(Name,'$STACK')])" --output text 2>/dev/null)"
info "CloudFront functions named for this stack: $nfun (expect 3, or 5 with custom domains)"

echo "== API Gateway =="
cors="$(aws apigatewayv2 get-apis --query "length(Items[?CorsConfiguration!=null])" --output text 2>/dev/null)"
check "no HTTP API has a CORS configuration (same-origin design)" "0" "$cors"
info "HTTP APIs in the region: $(aws apigatewayv2 get-apis --query 'length(Items)' --output text 2>/dev/null) (this stack should own 2 to 4 of them)"

echo "== Lambda =="
funcs="$(aws lambda list-functions --query "Functions[?contains(FunctionName,'$STACK')].FunctionName" --output text 2>/dev/null)"
for f in $funcs; do
  cfg="$(aws lambda get-function-configuration --function-name "$f" --query '[Runtime,Architectures[0],MemorySize,Timeout]' --output text 2>/dev/null | tr '\t' ' ')"
  info "$f: $cfg"
done
conc="$(aws lambda get-account-settings --query 'AccountLimit.ConcurrentExecutions' --output text 2>/dev/null)"
info "account Lambda concurrency quota: $conc"
if [ "${conc:-0}" -le 10 ] 2>/dev/null; then note "Lambda concurrency quota is ${conc}" "request an increase before a large class (Service Quotas)"; fi
nsd="$(aws lambda list-functions --query "length(Functions[?starts_with(FunctionName,'sd-')])" --output text 2>/dev/null)"
info "student functions (sd-*): $nsd"

echo "== IAM =="
SBX="$(clean "$(aws iam list-roles --query "Roles[?contains(RoleName,'${STACK}-StudentSandboxRole')].RoleName | [0]" --output text 2>/dev/null)")"
if [ -z "$SBX" ]; then bad "sandbox role found" "not found"; else
  bnd="$(clean "$(aws iam get-role --role-name "$SBX" --query 'Role.PermissionsBoundary.PermissionsBoundaryArn' --output text 2>/dev/null)")"
  if [ -n "$bnd" ]; then ok "sandbox role has a permissions boundary"; else bad "sandbox role permissions boundary" "none"; fi
  SBXARN="$(aws iam get-role --role-name "$SBX" --query Role.Arn --output text 2>/dev/null)"
  for a in s3:GetObject dynamodb:Scan ssm:GetParameter secretsmanager:GetSecretValue lambda:InvokeFunction iam:ListRoles; do
    d="$(aws iam simulate-principal-policy --policy-source-arn "$SBXARN" --action-names "$a" --resource-arns '*' --query 'EvaluationResults[0].EvalDecision' --output text 2>/dev/null)"
    check "sandbox role is denied $a" "implicitDeny" "$d"
  done
  d="$(aws iam simulate-principal-policy --policy-source-arn "$SBXARN" --action-names logs:PutLogEvents --resource-arns "arn:aws:logs:$AWS_REGION:$ACCOUNT:log-group:/aws/lambda/sd-probe:*" --query 'EvaluationResults[0].EvalDecision' --output text 2>/dev/null)"
  check "sandbox role may write sd-* logs" "allowed" "$d"
fi
DEP="$(clean "$(aws iam list-roles --query "Roles[?contains(RoleName,'${STACK}-DeployFunctionRole')].RoleName | [0]" --output text 2>/dev/null)")"
if [ -n "$DEP" ] && [ -n "${SBXARN:-}" ]; then
  DEPARN="$(aws iam get-role --role-name "$DEP" --query Role.Arn --output text 2>/dev/null)"
  d="$(aws iam simulate-principal-policy --policy-source-arn "$DEPARN" --action-names lambda:CreateFunction --resource-arns "arn:aws:lambda:$AWS_REGION:$ACCOUNT:function:not-a-student-function" --query 'EvaluationResults[0].EvalDecision' --output text 2>/dev/null)"
  check "DeployFunction cannot create a non-sd-* function" "implicitDeny" "$d"
  d="$(aws iam simulate-principal-policy --policy-source-arn "$DEPARN" --action-names iam:PassRole --resource-arns "$DEPARN" --context-entries ContextKeyName=iam:PassedToService,ContextKeyValues=lambda.amazonaws.com,ContextKeyType=string --query 'EvaluationResults[0].EvalDecision' --output text 2>/dev/null)"
  check "DeployFunction cannot pass its own role to Lambda" "implicitDeny" "$d"
  d="$(aws iam simulate-principal-policy --policy-source-arn "$DEPARN" --action-names s3:PutObject --resource-arns "arn:aws:s3:::${BUCKET:-x}/portal/starter-kit.zip" --query 'EvaluationResults[0].EvalDecision' --output text 2>/dev/null)"
  if [ "$d" = "allowed" ]; then note "DeployFunction can write portal/ (the kit and pages)" "finding F1 in 09: add an explicit deny and sign the kit"; else ok "DeployFunction cannot write portal/"; fi
fi

echo "== Data and secrets =="
T="$(out TokensTableName)"
if [ -n "$T" ]; then
  check "TokensTable on-demand" "PAY_PER_REQUEST" "$(aws dynamodb describe-table --table-name "$T" --query 'Table.BillingModeSummary.BillingMode' --output text 2>/dev/null)"
  check "TokensTable TTL enabled" "ENABLED" "$(aws dynamodb describe-time-to-live --table-name "$T" --query 'TimeToLiveDescription.TimeToLiveStatus' --output text 2>/dev/null)"
  check "TokensTable PITR enabled" "ENABLED" "$(aws dynamodb describe-continuous-backups --table-name "$T" --query 'ContinuousBackupsDescription.PointInTimeRecoveryDescription.PointInTimeRecoveryStatus' --output text 2>/dev/null)"
else bad "tokens table output present" "missing"; fi
tp="$(aws ssm describe-parameters --parameter-filters 'Key=Name,Option=BeginsWith,Values=/student-deploy' --query 'Parameters[].[Name,Type]' --output text 2>/dev/null | tr '\t' ':' | tr '\n' ' ')"
info "SSM parameters (name:type): ${tp:-none}"
case "$tp" in *teacher:String*) note "the teacher login is an SSM String parameter" "finding F3 in 09: a SecureString with a KMS key is stronger";; esac
if aws secretsmanager describe-secret --secret-id openrouter >/dev/null 2>&1; then ok "AI secret 'openrouter' exists"; else note "AI secret 'openrouter' not found" "run the secrets command if you use class AI"; fi

echo "== Operations =="
na="$(aws cloudwatch describe-alarms --alarm-name-prefix "$STACK" --query 'length(MetricAlarms)' --output text 2>/dev/null)"
if [ "${na:-0}" -ge 5 ]; then ok "alarms present ($na)"; else note "alarms present: ${na:-0}" "set the alert-email parameter to create them"; fi
topic="$(out AlertsTopicArn)"
if [ -n "$topic" ]; then
  pend="$(aws sns list-subscriptions-by-topic --topic-arn "$topic" --query "length(Subscriptions[?SubscriptionArn=='PendingConfirmation'])" --output text 2>/dev/null)"
  check "no SNS subscription is pending confirmation" "0" "$pend"
else note "no alerts topic" "alarms cannot email you"; fi
noret="$(aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/$STACK" --query 'length(logGroups[?retentionInDays==null])' --output text 2>/dev/null)"
if [ "${noret:-0}" -gt 0 ]; then note "$noret platform log group(s) never expire" "set a retention period (08, part 3.2)"; else ok "platform log groups have retention"; fi
bud="$(aws budgets describe-budgets --account-id "$ACCOUNT" --query 'length(Budgets)' --output text 2>/dev/null)"
if [ "${bud:-0}" -ge 1 ] 2>/dev/null; then ok "an AWS Budget exists"; else note "no AWS Budget found (or no permission to list)" "create one: the template does not"; fi
anom="$(aws ce get-anomaly-monitors --query 'length(AnomalyMonitors)' --output text 2>/dev/null)"
if [ "${anom:-0}" -ge 1 ] 2>/dev/null; then ok "Cost Anomaly Detection monitor exists"; else note "no Cost Anomaly Detection monitor" "free and worth creating"; fi

echo
printf 'Result: %s passed, %s failed, %s warnings\n' "$pass" "$fail" "$warn"
[ "$fail" -eq 0 ]
