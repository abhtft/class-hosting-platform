"""Guards the Chatwoot adapter in template.yaml: off by default and independent of student backends, everything about it exists only when it is on, its own API with
its own throttle, a receiver that may only write its table, start the one worker and read the chatwoot/ secrets, a worker that may only update its table, read the same chatwoot/ secrets and call the one
Chatwoot address, and is never retried by the platform or reachable from the internet, and nobody else (no student role, no existing function) gains anything."""
import os
import re

root = os.path.join(os.path.dirname(__file__), "..")
template = open(os.path.join(root, "template.yaml"), encoding="utf8").read().replace("\r\n", "\n")


def block(name):
    start = template.index(f"\n  {name}:\n") + 1
    end = re.search(r"\n(?:  [A-Za-z]+:\n|  # -|\w+:\n|\n  # )", template[start + 1:])
    return template[start:start + 1 + end.start()] if end else template[start:]


# ---- off by default, and it does not need student backends ----
params = template[template.index("\nParameters:"):template.index("\nConditions:")]
assert re.search(r"  ChatwootEnabled:\n    Type: String\n    Default: 'false'\n    AllowedValues: \['false', 'true'\]", params), "the adapter is off unless asked for"
assert "UseChatwoot: !Equals [!Ref ChatwootEnabled, 'true']" in template, "its own switch, not tied to backends"
for name in ("ChatwootTable", "ChatwootMemoryTable", "ChatwootApi", "ChatwootReceiverFunction", "ChatwootWorkerFunction", "ChatwootSweeperFunction", "ChatwootAdminFunction"):
    assert "Condition: UseChatwoot" in block(name), f"{name} exists only when the adapter is on"
assert "ChatwootWebhookUrl:\n    Condition: UseChatwoot" in template, "its output exists only when it is on"
globals_ = template[template.index("\nGlobals:"):template.index("\nResources:")]
assert "CHATWOOT" not in globals_, "the table and worker names go to the functions that need them only: in Globals every function would depend on them"

# ---- the table: ids and times only, expiring by itself ----
table = block("ChatwootTable")
assert "AWS::DynamoDB::Table" in table and "BillingMode: PAY_PER_REQUEST" in table
assert re.search(r"AttributeName: pk\n\s+KeyType: HASH", table) and "RANGE" not in table
assert re.search(r"TimeToLiveSpecification:\n\s+AttributeName: ttl\n\s+Enabled: true", table), "every item expires by itself"
assert "PointInTimeRecoveryEnabled: true" in table
assert "DeletionPolicy" not in table, "it holds only short-lived de-duplication records: nothing to keep"

# ---- the conversation memory: its own table, expiring, NO point-in-time recovery (customers' words must not sit in backups), only the worker touches it ----
memory_table = block("ChatwootMemoryTable")
assert "AWS::DynamoDB::Table" in memory_table and "BillingMode: PAY_PER_REQUEST" in memory_table
assert re.search(r"AttributeName: pk\n\s+KeyType: HASH", memory_table) and "RANGE" not in memory_table
assert re.search(r"TimeToLiveSpecification:\n\s+AttributeName: ttl\n\s+Enabled: true", memory_table), "every item expires by itself"
assert "PointInTimeRecovery" not in memory_table.split("Properties:")[1], "no point-in-time recovery: expired words must not live on in backups"
assert re.search(r"CKV_AWS_28\n?\s+comment:", memory_table) or "id: CKV_AWS_28" in memory_table, "the scan's rule about recovery is skipped here on purpose, in the resource, with its reason"
assert "DeletionPolicy" not in memory_table, "short-lived: nothing to keep"
assert "ChatwootMemoryTable" not in block("ChatwootReceiverFunction") and "ChatwootMemoryTable" not in block("ChatwootAdminFunction"), "only the worker reads or writes the memory"

# ---- its own API: the webhook route, its own throttle, not the student backend API ----
api = block("ChatwootApi")
assert "AWS::Serverless::HttpApi" in api and re.search(r"ThrottlingRateLimit: \d+", api) and re.search(r"ThrottlingBurstLimit: \d+", api), "its own throttle"
receiver = block("ChatwootReceiverFunction")
assert "Handler: chatwoot_api.handler" in receiver and "CodeUri: lambda/" in receiver and "Runtime: python3.12" in receiver
assert re.search(r"Path: /hooks/chatwoot/\{agentId\}\n\s+Method: POST", receiver) and "ApiId: !Ref ChatwootApi" in receiver, "one route, POST only"
assert receiver.count("Type: HttpApi") == 1
assert "BackendApi" not in receiver and "GatewayFunction" not in receiver and "/b/{slug}" not in receiver, "it does not go through the student gateway"
assert "CHATWOOT_TABLE: !Ref ChatwootTable" in receiver and "CHATWOOT_WORKER_FUNCTION: !Ref ChatwootWorkerFunction" in receiver

# ---- the receiver: write its table, start the worker, read the chatwoot/ secrets, nothing else ----
assert re.search(r"Action: \[dynamodb:PutItem, dynamodb:UpdateItem, dynamodb:DeleteItem\]\n\s+Resource: !GetAtt ChatwootTable\.Arn\n\s+Condition:\n\s+ForAllValues:StringLike:\n\s+dynamodb:LeadingKeys: \['msg#\*'\]", receiver), \
    "its table: write one message item, count a repeat, remove on a failed hand-over, and nothing of the registry"
assert re.search(r"Action: dynamodb:GetItem\n\s+Resource: !GetAtt ChatwootTable\.Arn\n\s+Condition:\n\s+ForAllValues:StringLike:\n\s+dynamodb:LeadingKeys: \['agent#\*', 'tenant#\*'\]", receiver), "it may read the registry (single items)"
assert re.search(r"Action: lambda:InvokeFunction\n\s+Resource: !GetAtt ChatwootWorkerFunction\.Arn", receiver) and receiver.count("lambda:") == 1, "it may start the worker and nothing else"
assert re.search(r"Action: secretsmanager:GetSecretValue\n\s+Resource: !Sub 'arn:\$\{AWS::Partition\}:secretsmanager:\$\{AWS::Region\}:\$\{AWS::AccountId\}:secret:chatwoot/\*'", receiver), \
    "it reads only secrets named chatwoot/..."
assert receiver.count("Action: secretsmanager:") == 1
code = re.sub(r"#[^\n]*", "", receiver)
for forbidden in ("dynamodb:Scan", "dynamodb:Query", "dynamodb:BatchWriteItem", "dynamodb:*", "TokensTable", "SitesBucket", "s3:", "kms:", "iam:", "ssm:", "logs:",
                  "cloudfront", "StudentDataTable", "TracesTable", "KnowledgeWorker", "AiSecretName", "Resource: '*'", "Action: '*'"):
    assert forbidden not in code, f"the receiver must not have {forbidden}"

# ---- the worker: its message items and spend counters, the registry, the shelves of the businesses, the Google key; never retried, not reachable from the internet ----
worker = block("ChatwootWorkerFunction")
assert "Handler: chatwoot_api.worker" in worker and "CodeUri: lambda/" in worker and "Runtime: python3.12" in worker and "MemorySize: 1024" in worker, "a shelf is loaded into memory to be searched"
assert re.search(r"EventInvokeConfig:\n\s+MaximumRetryAttempts: 0", worker), "no automatic second run: the message item decides what has been done (an event may still arrive twice, the worker claims it once)"
assert "Events:" not in worker and "HttpApi" not in worker, "nothing on the internet reaches the worker directly"
assert "VpcConfig" not in worker, "it must reach Chatwoot and Google, so it is not in a private network without a way out"
assert "CHATWOOT_TABLE: !Ref ChatwootTable" in worker and "CHATWOOT_MEMORY_TABLE: !Ref ChatwootMemoryTable" in worker
assert "CHATWOOT_BASE_URL: !Ref ChatwootBaseUrl" in worker and "CHATWOOT_AUTH_STYLE: !Ref ChatwootAuthStyle" in worker, "the one Chatwoot address and the header style come from parameters"
assert re.search(r"Action: dynamodb:UpdateItem\n\s+Resource: !GetAtt ChatwootTable\.Arn\n\s+Condition:\n\s+ForAllValues:StringLike:\n\s+dynamodb:LeadingKeys: \['msg#\*', 'usage#\*'\]", worker), "it updates message items and spend counters only"
assert re.search(r"Action: dynamodb:GetItem\n\s+Resource: !GetAtt ChatwootTable\.Arn\n\s+Condition:\n\s+ForAllValues:StringLike:\n\s+dynamodb:LeadingKeys: \['agent#\*', 'tenant#\*', 'usage#\*', 'suite#\*', 'suitestate#\*', 'suiterun#\*'\]", worker), "and reads the registry, the spend counters and, for a suite run, the suite, its runs and its state"
assert re.search(r"Action: dynamodb:PutItem\n\s+Resource: !GetAtt ChatwootTable\.Arn\n\s+Condition:\n\s+ForAllValues:StringLike:\n\s+dynamodb:LeadingKeys: \['suiterun#\*', 'suitestate#\*'\]", worker), "and writes a suite run's progress and what it found, nothing else"
assert re.search(r"Action: \[dynamodb:GetItem, dynamodb:PutItem\]\n\s+Resource: !GetAtt ChatwootMemoryTable\.Arn\n\s+Condition:\n\s+ForAllValues:StringLike:\n\s+dynamodb:LeadingKeys: \['mem#\*'\]", worker), "it reads and writes the conversation memory items (mem#...) of the memory table and nothing else there"
assert re.search(r"Action: s3:GetObject\n\s+Resource: !Sub '\$\{SitesBucket\.Arn\}/knowledge/tenant/\*'", worker), "it reads the shelves of the businesses and nothing else in the bucket"
assert re.search(r"Action: s3:ListBucket\n\s+Resource: !GetAtt SitesBucket\.Arn\n\s+Condition:\n\s+StringLike:\n\s+s3:prefix: \['knowledge/tenant/\*'\]", worker), "listing is limited to the same prefix"
assert re.search(r"Action: secretsmanager:GetSecretValue\n\s+Resource: !Sub 'arn:\$\{AWS::Partition\}:secretsmanager:\$\{AWS::Region\}:\$\{AWS::AccountId\}:secret:\$\{AiSecretName\}-\?\?\?\?\?\?'", worker), "the platform's Google key, this one secret"
assert re.search(r"Action: secretsmanager:GetSecretValue\n\s+Resource: !Sub 'arn:\$\{AWS::Partition\}:secretsmanager:\$\{AWS::Region\}:\$\{AWS::AccountId\}:secret:chatwoot/\*'", worker), "the bot token and account id: secrets named chatwoot/..."
assert worker.count("Action: secretsmanager:") == 2, "exactly two secret statements: the bot token and the platform's Google key"
code = re.sub(r"#[^\n]*", "", worker)
assert sorted(re.findall(r"Action: ([\w:]+)", code)) == ["bedrock:InvokeModel", "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "s3:GetObject", "s3:GetObject", "s3:ListBucket", "secretsmanager:GetSecretValue", "secretsmanager:GetSecretValue"], "exactly these single-action permissions"
bedrock_resources = re.findall(r"bedrock:[^\s']*", code)
assert sorted(bedrock_resources) == sorted(['bedrock:*:${AWS::AccountId}:inference-profile/us.anthropic.claude-haiku-5-5', 'bedrock:*:${AWS::AccountId}:inference-profile/us.anthropic.claude-sonnet-5-5', 'bedrock:*::foundation-model/anthropic.claude-haiku-5-5', 'bedrock:*::foundation-model/anthropic.claude-sonnet-5-5', 'bedrock:InvokeModel']), "Bedrock: asking the two offered Claude models, nothing else of Bedrock"
assert re.findall(r"Action: \[([\w:, ]+)\]", code) == ["dynamodb:GetItem, dynamodb:PutItem"], "and one pair, on the memory table only"
assert re.findall(r"\$\{SitesBucket\.Arn\}/[^']*", code) == ["${SitesBucket.Arn}/knowledge/tenant/*", "${SitesBucket.Arn}/chatwoot-media/*"], "objects under knowledge/tenant/ (read the shelves) and chatwoot-media/ (read the owners' media) only: not the class library, not a student's shelf, not a site"
for forbidden in ("lambda:", "dynamodb:Scan", "dynamodb:Query", "dynamodb:DeleteItem", "dynamodb:*", "s3:PutObject", "s3:DeleteObject", "s3:*", "TokensTable", "kms:", "iam:", "ssm:", "cloudfront",
                  "StudentDataTable", "TracesTable", "Resource: '*'", "Action: '*'", "knowledge/student", "knowledge/class", "sites/", "portal/"):
    assert forbidden not in code, f"the worker must not have {forbidden}"
assert template.count("bedrock:InvokeModel") == 2, "only the worker and the AI proxy may ask Bedrock: no student role, no other function"
base_url_parameter = re.search(r"  ChatwootBaseUrl:\n((?:    [^\n]*\n|      [^\n]*\n)*)", params).group(1)
assert "Default: ''" in base_url_parameter and "AllowedPattern: '^(https://" in base_url_parameter, "https only, empty until the teacher sets it"
auth_style_parameter = re.search(r"  ChatwootAuthStyle:\n((?:    [^\n]*\n|      [^\n]*\n)*)", params).group(1)
assert "Default: bearer" in auth_style_parameter and "AllowedValues: [bearer, api_access_token]" in auth_style_parameter
assert "CHATWOOT_BASE_URL" not in block("ChatwootReceiverFunction"), "the receiver never calls out"
assert "secretsmanager" not in block("ChatwootAdminFunction") and "s3:" not in block("ChatwootReceiverFunction") and "knowledge" not in block("ChatwootReceiverFunction"), "only the worker reads shelves and the model's key"

# ---- the sweeper: message items only, the one worker, on a schedule; no secrets, no Chatwoot, no memory table, nothing else ----
sweeper = block("ChatwootSweeperFunction")
assert "Handler: chatwoot_sweeper.handler" in sweeper and "CodeUri: lambda/" in sweeper and "Runtime: python3.12" in sweeper and "MemorySize: 256" in sweeper
assert re.search(r"Type: Schedule\n\s+Properties:\n\s+Schedule: rate\(10 minutes\)", sweeper) and sweeper.count("Type: Schedule") == 1 and "HttpApi" not in sweeper, "a schedule, nothing on the internet"
assert "CHATWOOT_TABLE: !Ref ChatwootTable" in sweeper and "CHATWOOT_WORKER_FUNCTION: !Ref ChatwootWorkerFunction" in sweeper and "CHATWOOT_MEMORY_TABLE" not in sweeper
assert re.search(r"Action: dynamodb:Scan\n\s+Resource: !GetAtt ChatwootTable\.Arn", sweeper), "it scans the adapter's own table to find stuck message items"
assert re.search(r"Action: dynamodb:UpdateItem\n\s+Resource: !GetAtt ChatwootTable\.Arn\n\s+Condition:\n\s+ForAllValues:StringLike:\n\s+dynamodb:LeadingKeys: \['msg#\*'\]", sweeper), "and marks message items only"
assert re.search(r"Action: lambda:InvokeFunction\n\s+Resource: !GetAtt ChatwootWorkerFunction\.Arn", sweeper) and sweeper.count("lambda:InvokeFunction") == 1, "it may start the one worker"
code = re.sub(r"#[^\n]*", "", sweeper)
assert sorted(re.findall(r"Action: ([\w:]+)", code)) == ["dynamodb:Scan", "dynamodb:UpdateItem", "lambda:InvokeFunction"], "exactly these permissions"
for forbidden in ("secretsmanager", "s3:", "ssm:", "kms:", "iam:", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:GetItem", "dynamodb:Query", "dynamodb:*", "TokensTable", "StudentDataTable", "TracesTable", "ChatwootMemoryTable", "CHATWOOT_BASE_URL",
                  "SitesBucket", "Resource: '*'", "Action: '*'"):
    assert forbidden not in code, f"the sweeper must not have {forbidden}"
assert "ChatwootSweeperFunction" not in block("ChatwootReceiverFunction") and "ChatwootSweeperFunction" not in block("ChatwootWorkerFunction"), "nothing depends on the sweeper"

# ---- the admin function: the registry, and the one worker for Studio's dry run; no secrets, no Chatwoot, nothing else ----
admin = block("ChatwootAdminFunction")
assert "Handler: chatwoot_admin.handler" in admin and "CodeUri: lambda/" in admin and "Runtime: python3.12" in admin
assert re.search(r"Path: /admin/\{proxy\+\}\n\s+Method: ANY", admin) and "ApiId: !Ref ChatwootApi" in admin and admin.count("Type: HttpApi") == 1, "one route family, on the adapter's own API"
assert "/hooks/chatwoot" not in admin and "BackendApi" not in admin and "GatewayFunction" not in admin
assert "CHATWOOT_TABLE: !Ref ChatwootTable" in admin and "CHATWOOT_BASE_URL" not in admin and "CHATWOOT_AUTH_STYLE" not in admin and "CHATWOOT_WORKER_FUNCTION: !Ref ChatwootWorkerFunction" in admin and "CHATWOOT_MEMORY_TABLE" not in admin
assert re.search(r"Timeout: 29", admin), "it waits for the worker's dry run"
assert re.search(r"Action: lambda:InvokeFunction\n\s+Resource: !GetAtt ChatwootWorkerFunction\.Arn", admin) and admin.count("lambda:InvokeFunction") == 1, "it may start the one worker"
assert re.search(r"Action: \[dynamodb:GetItem, dynamodb:PutItem\]\n\s+Resource: !GetAtt ChatwootTable\.Arn\n\s+Condition:\n\s+ForAllValues:StringLike:\n\s+dynamodb:LeadingKeys: \['tenant#\*', 'agent#\*', 'suite#\*', 'suitestate#\*', 'suiterun#\*', 'teach#\*', 'teachidx#\*'\]", admin), "the registry, the suite's and the teach sessions' items, one at a time"
assert re.search(r"Action: dynamodb:DeleteItem\n\s+Resource: !GetAtt ChatwootTable\.Arn\n\s+Condition:\n\s+ForAllValues:StringLike:\n\s+dynamodb:LeadingKeys: \['agent#\*', 'suiterun#\*'\]", admin), "it may remove an agent item and a run it could not start, nothing else"
code = re.sub(r"#[^\n]*", "", admin)
assert sorted(re.findall(r"Action: (\[[^\]]*\]|[\w:]+)", code)) == ["[dynamodb:GetItem, dynamodb:PutItem]", "[s3:PutObject, s3:GetObject]", "dynamodb:DeleteItem", "lambda:InvokeFunction"], "exactly these permissions"
assert re.search(r"Action: \[s3:PutObject, s3:GetObject\]\n\s+Resource: !Sub '\$\{SitesBucket\.Arn\}/chatwoot-media/\*'", admin) and re.findall(r"\$\{SitesBucket\.Arn\}/[^']*", code) == ["${SitesBucket.Arn}/chatwoot-media/*"], "the admin function writes and checks the owners' media files under chatwoot-media/ only (it signs the upload forms)"
for forbidden in ("secretsmanager:", "dynamodb:Scan", "dynamodb:Query", "dynamodb:UpdateItem", "dynamodb:BatchWriteItem", "dynamodb:*", "msg#", "TokensTable", "s3:DeleteObject", "s3:ListBucket", "s3:*", "kms:", "ChatwootMemoryTable",
                  "iam:", "ssm:", "cloudfront", "StudentDataTable", "TracesTable", "AiSecretName", "Resource: '*'", "Action: '*'", "VpcConfig"):
    assert forbidden not in code, f"the admin function must not have {forbidden}"
assert "ChatwootAdminFunction" not in block("ChatwootReceiverFunction") and "/admin" not in block("ChatwootReceiverFunction"), "the webhook function does not serve the admin routes"
assert re.search(r"ChatwootTableName:\n    Condition: UseChatwoot\n    Description: [^\n]+\n    Value: !Ref ChatwootTable\n", template), "the table name is an output only when the adapter is on"

# ---- nobody else gains anything: no student role, no existing function mentions the adapter ----
for name in ("StudentSandboxRole", "StudentSandboxBoundary", "GatewayFunction", "DeployFunction", "SweeperFunction", "KnowledgeFunction", "KnowledgeWorkerFunction", "AiProxyFunction",
             "JobRunnerFunction", "StorageFunction", "BackendApi"):
    assert "Chatwoot" not in block(name) and "chatwoot" not in block(name), f"{name} must not know about the adapter"
assert not re.search(r"secretsmanager:\*|secret:\*", template)

# ---- the address is an output only when on ----
assert re.search(r"ChatwootWebhookUrl:\n    Condition: UseChatwoot\n    Description: [^\n]+\n    Value: !Sub 'https://\$\{ChatwootApi\}\.execute-api\.\$\{AWS::Region\}\.\$\{AWS::URLSuffix\}/hooks/chatwoot'", template)

print("test_template_chatwoot.py: the Chatwoot adapter is off by default, scoped to its own table, secrets and worker, and nobody else gains anything")
