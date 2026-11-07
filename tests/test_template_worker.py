"""Guards the compute worker in template.yaml: off by default and nothing exists while it is off; the machine has no inbound access, no key pair, an encrypted disk,
IMDSv2 only, stops (not deletes) when it shuts down; its role reaches only the worker/ folder of the bucket; the portal and the control function may start and stop
THAT machine and nothing else; the control function runs every 15 minutes and touches only worker# keys; and no student function gets anything."""
import os
import re

root = os.path.join(os.path.dirname(__file__), "..")
template = open(os.path.join(root, "template.yaml"), encoding="utf8").read().replace("\r\n", "\n")


def block(name):
    start = template.index(f"\n  {name}:\n") + 1
    end = re.search(r"\n(?:  [A-Za-z]+:\n|  # -|\w+:\n|\n  # )", template[start + 1:])
    return template[start:start + 1 + end.start()] if end else template[start:]


params = template[template.index("\nParameters:"):template.index("\nConditions:")]
assert re.search(r"  WorkerEnabled:\n    Type: String\n    Default: 'false'\n    AllowedValues: \['false', 'true'\]", params), "the worker is off unless asked for"
assert re.search(r"  WorkerAmiId:\n    Type: String\n    Default: ''", params), "the image is chosen by the installer, once"
assert re.search(r"  WorkerInstanceType:\n    Type: String\n    Default: m7i-flex.large\n    AllowedValues: \[m7i-flex.large, c7i-flex.large, t3.small, t4g.small, c7g.large, c7g.xlarge, c7g.2xlarge\]", params), "the default is a type a Free plan account may use"
assert re.search(r"  WorkerDiskGb:\n    Type: Number\n    Default: 30\n    MinValue: 20\n    MaxValue: 200", params)
assert re.search(r"  WorkerMaxMinutes:\n    Type: Number\n    Default: 60\n    MinValue: 15\n    MaxValue: 360", params)
assert "UseWorker: !Equals [!Ref WorkerEnabled, 'true']" in template

for name in ("WorkerSecurityGroup", "WorkerRole", "WorkerInstanceProfile", "WorkerInstance", "WorkerControlFunction"):
    assert "Condition: UseWorker" in block(name), f"{name} exists only when the worker is on"
assert "WorkerInstanceId:\n    Condition: UseWorker" in template

# ---- the machine
machine = block("WorkerInstance")
assert "ImageId: !Ref WorkerAmiId" in machine and "InstanceType: !Ref WorkerInstanceType" in machine
assert "InstanceInitiatedShutdownBehavior: stop" in machine, "shutting down stops it, it never deletes the machine"
assert re.search(r"VolumeType: gp3\n\s+VolumeSize: !Ref WorkerDiskGb\n\s+Encrypted: true", machine), "an encrypted disk"
assert re.search(r"HttpTokens: required", machine), "IMDSv2 only"
assert "KeyName" not in machine and "SSH" not in machine and "22" not in re.findall(r"FromPort: (\d+)", machine), "no key pair, no SSH"
assert "AssociatePublicIpAddress" not in machine
sg = block("WorkerSecurityGroup")
assert "SecurityGroupIngress: []" in sg and "FromPort" not in sg, "no inbound access at all"

# ---- the machine's own role
role = block("WorkerRole")
assert "AmazonSSMManagedInstanceCore" in role, "the terminal connection"
objects = re.findall(r"\$\{SitesBucket\.Arn\}/[^']*", role)
assert objects == ["${SitesBucket.Arn}/worker/*"], f"the machine reaches the worker/ folder only, found {objects}"
assert re.search(r"Action: \[s3:GetObject, s3:PutObject\]", role) and "s3:DeleteObject" not in role
assert re.search(r"s3:prefix: \['worker/\*'\]", role)
for forbidden in ("dynamodb", "secretsmanager", "iam:PassRole", "iam:Create", "kms:", "lambda:", "cloudfront", "ec2:", "ssm:GetParameter", "Action: '*'", "Action: \"*\""):
    assert forbidden not in role, f"the worker role must not have {forbidden} yet"

# ---- the control function
ctl = block("WorkerControlFunction")
assert "Handler: worker.handler" in ctl and re.search(r"Schedule: rate\(15 minutes\)", ctl), "the watchdog runs every 15 minutes"
assert re.search(r"Action: \[ec2:StartInstances, ec2:StopInstances\]\n\s+Resource: !Sub 'arn:\$\{AWS::Partition\}:ec2:\$\{AWS::Region\}:\$\{AWS::AccountId\}:instance/\$\{WorkerInstance\}'", ctl), "start and stop that one machine"
assert re.search(r"Action: ec2:DescribeInstances\n\s+Resource: '\*'", ctl)
assert re.search(r"Action: \[dynamodb:GetItem, dynamodb:PutItem, dynamodb:UpdateItem, dynamodb:DeleteItem\]\n\s+Resource: !GetAtt TokensTable\.Arn\n\s+Condition:\n\s+ForAllValues:StringLike:\n\s+dynamodb:LeadingKeys: \['worker#\*'\]", ctl)
for forbidden in ("dynamodb:Scan", "dynamodb:Query", "ec2:RunInstances", "ec2:TerminateInstances", "ec2:Create", "ec2:Modify", "iam:", "secretsmanager", "kms:", "StudentDataTable", "TracesTable"):
    assert forbidden not in ctl, f"the control function must not have {forbidden}"
assert re.findall(r"\$\{SitesBucket\.Arn\}/[^']*", ctl) == ["${SitesBucket.Arn}/worker/*"]

# ---- the portal function: the buttons and the page, for this one machine
portal_fn = block("DeployFunction")
assert "WORKER_INSTANCE_ID: !If [UseWorker, !Ref WorkerInstance, '']" in portal_fn
start_stop = re.findall(r"- UseWorker\n\s+- Effect: Allow\n\s+Action: (\[ec2:StartInstances, ec2:StopInstances\])\n\s+Resource: (!Sub 'arn:[^']*instance/\$\{WorkerInstance\}')", portal_fn)
assert len(start_stop) == 1, "the portal may start and stop only the worker machine"
assert "ec2:RunInstances" not in portal_fn and "ec2:TerminateInstances" not in portal_fn

# ---- nobody else is given the machine or its folder
for name in ("KnowledgeFunction", "KnowledgeWorkerFunction", "AiProxyFunction", "StorageFunction", "GatewayFunction", "JobRunnerFunction"):
    fn = block(name)
    assert "WorkerInstance" not in fn and "ec2:" not in fn and "/worker/" not in fn, f"{name} gets nothing of the worker"
print("ok")
