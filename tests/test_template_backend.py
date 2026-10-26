"""Guards for the student-backend infrastructure in template.yaml: the sandbox stays logs-only, student-function management
stays scoped to sd-*, the gateway can do no more than it needs, and everything is switchable off. Reads the text of the
template (CloudFormation tags make it awkward to parse as YAML) and asserts on the relevant blocks."""
import os
import re

template = open(os.path.join(os.path.dirname(__file__), "..", "template.yaml"), encoding="utf8").read().replace("\r\n", "\n")


def block(name):
    """Text of one top-level resource: from '  Name:' to the next resource key or section comment at the same indent."""
    start = template.index(f"\n  {name}:\n") + 1
    end = re.search(r"\n(?:  [A-Za-z]+:\n|  # -|\w+:\n)", template[start + 1:])
    return template[start:start + 1 + end.start()] if end else template[start:]


def actions(text):
    found = re.findall(r"\b((?:logs|lambda|iam|dynamodb|s3|sts|ssm|cloudfront|acm|ec2|kms|secretsmanager|\*):[A-Za-z*]+)", text)
    return sorted(set(found))


# ---- the sandbox every student function runs as: logs only, bounded by a permission boundary ----
role, boundary = block("StudentSandboxRole"), block("StudentSandboxBoundary")
allowed = {"logs:CreateLogStream", "logs:PutLogEvents"}
role_actions = {a for a in actions(role) if not a.startswith("sts:")}
assert role_actions == allowed, role_actions
assert set(actions(boundary)) == allowed, actions(boundary)
assert "PermissionsBoundary: !Ref StudentSandboxBoundary" in role, "the role must carry its boundary"
assert "log-group:/aws/lambda/sd-*" in role and "Resource: '*'" not in role and "Resource: '*'" not in boundary
assert "lambda.amazonaws.com" in role, "only Lambda can assume it"

# ---- the control plane (Deploy Lambda): student functions only by name prefix, one role only, one condition ----
deploy = block("DeployFunction")
start = deploy.index("student backends: manage ONLY functions named sd-*")
statement = deploy[start - 40:deploy.index("logs:CreateLogGroup")]
lambda_actions = [a for a in actions(statement) if a.startswith("lambda:")]
assert "lambda:CreateFunction" in lambda_actions and not any(a in ("lambda:*", "lambda:InvokeFunction", "lambda:AddPermission", "lambda:CreateFunctionUrlConfig") for a in lambda_actions), lambda_actions
assert "function:sd-*" in statement and "Resource: '*'" not in statement
assert "Action: iam:PassRole" in statement and "Resource: !GetAtt StudentSandboxRole.Arn" in statement
assert "'iam:PassedToService': lambda.amazonaws.com" in statement, "PassRole must be limited to Lambda"
assert "iam:*" not in deploy and "iam:CreateRole" not in deploy and "iam:AttachRolePolicy" not in deploy, "the Deploy Lambda must never create or change roles"
log_statement = deploy[deploy.index("logs:CreateLogGroup") - 40:deploy.index("logs:CreateLogGroup") + 700]
assert "log-group:/aws/lambda/sd-*" in log_statement and "log-group:*" not in log_statement.replace("log-group:/aws", "")

# ---- the gateway: reads and counts in DynamoDB, invokes only sd-* functions, nothing else ----
gateway = block("GatewayFunction")
assert set(actions(gateway)) == {"dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "lambda:InvokeFunction"}, actions(gateway)
assert "function:sd-*" in gateway and "Resource: '*'" not in gateway
assert "Handler: gateway.handler" in gateway and "ANY" in gateway and "/b/{slug}/{proxy+}" in gateway

# ---- no dependency loop: the gateway must not reference the distribution that references the Backend API ----
assert "DISTRIBUTION_ID: ''" in gateway and "DISTRIBUTION_DOMAIN: ''" in gateway, "override the shared Globals references (circular dependency)"
assert "!Ref Distribution" not in gateway and "!GetAtt Distribution" not in gateway

# ---- everything public-facing is switchable off, and off is the default ----
for name in ("BackendApi", "GatewayFunction", "BackendRouteFunction"):
    assert "Condition: UseBackend" in block(name), name
assert re.search(r"BackendEnabled:\n    Type: String\n    Default: 'false'", template), "dark by default"
distribution = block("Distribution")
assert "!If\n          - UseBackend" in distribution and "x-origin-secret" in distribution, "the /api/* origin carries the secret header"
assert "PathPattern: /api/*" in distribution and "CachingDisabled" in distribution and "AllViewerExceptHostHeader" in distribution

# ---- the origin secret reaches the gateway through the shared environment ----
assert "ORIGIN_SECRET: !Select [2, !Split ['/', !Ref 'AWS::StackId']]" in template

# ---- no wildcard action anywhere in the backend blocks ----
for name in ("StudentSandboxRole", "StudentSandboxBoundary", "GatewayFunction", "BackendApi"):
    assert "Action: '*'" not in block(name) and "lambda:*" not in block(name) and "logs:*" not in block(name), name

print("test_template_backend.py: all passed")
