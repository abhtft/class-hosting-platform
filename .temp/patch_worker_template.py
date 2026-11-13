p = r"D:\Github_Codes\class-hosting-platform-github\template.yaml"
s = open(p, encoding="utf8").read()


def rep(a, b):
    global s
    assert a in s, a[:70]
    s = s.replace(a, b, 1)


# ---- parameters
rep('''  KnowledgeMonthlyCapUsd:''', '''  WorkerEnabled:
    Type: String
    Default: 'false'
    AllowedValues: ['false', 'true']
    Description: >
      Compute worker: one small EC2 machine that is OFF by default. The teacher starts and stops it from the teacher page (and may let it start once a
      day), it runs platform-owned checks (never student code), writes a report to S3 and shuts itself down; a watchdog stops it if it ever runs too
      long. Costs a few dollars a month at 30 minutes a day (the machine only while it runs, the disk always). Off by default: with it off nothing
      is created. See docs/COMPUTE_WORKER_PLAN.md.
  WorkerAmiId:
    Type: String
    Default: ''
    Description: Set by the installer (the current Amazon Linux 2023 ARM image, chosen once so a later image release never replaces the machine). Leave empty.
  WorkerInstanceType:
    Type: String
    Default: c7g.xlarge
    AllowedValues: [c7g.large, c7g.xlarge, c7g.2xlarge, t4g.medium, t4g.large]
    Description: The machine size (ARM, like the platform's functions). c7g.xlarge has 4 vCPUs and 8 GB of memory.
  WorkerDiskGb:
    Type: Number
    Default: 30
    MinValue: 20
    MaxValue: 200
    Description: Size of the machine's encrypted disk in GB. It is kept while the machine is stopped (about 8 cents a GB a month).
  WorkerMaxMinutes:
    Type: Number
    Default: 60
    MinValue: 15
    MaxValue: 360
    Description: The longest the machine may stay on after a start. The watchdog stops it after this (the teacher can lower it on the page).
  KnowledgeMonthlyCapUsd:''')
rep("  UseTraces: !And [!Condition UseBackend, !Equals [!Ref TracesEnabled, 'true']]\n",
    "  UseTraces: !And [!Condition UseBackend, !Equals [!Ref TracesEnabled, 'true']]\n  UseWorker: !Equals [!Ref WorkerEnabled, 'true']\n")

# ---- resources, placed before the CDN section
rep('''  # ---------- CDN ----------
  SitesOAC:''', '''  # ---------- Compute worker (worker.py): one small machine, off by default ----------
  # Nothing can reach it from the internet (no inbound rule, no key pair); the teacher's terminal reaches it through Systems Manager. It may read and write
  # only the worker/ folder of the sites bucket. It is started and stopped by the portal and the control function below, and nothing else.
  WorkerSecurityGroup:
    Type: AWS::EC2::SecurityGroup
    Condition: UseWorker
    Properties:
      GroupDescription: Compute worker. No inbound access at all.
      SecurityGroupIngress: []

  WorkerRole:
    Type: AWS::IAM::Role
    Condition: UseWorker
    Properties:
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: ec2.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - !Sub 'arn:${AWS::Partition}:iam::aws:policy/AmazonSSMManagedInstanceCore' # the terminal connection (no SSH)
      Policies:
        - PolicyName: worker-folder
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow # task code, settings and reports: worker/ only (never sites/, portal/ or anything else)
                Action: [s3:GetObject, s3:PutObject]
                Resource: !Sub '${SitesBucket.Arn}/worker/*'
              - Effect: Allow
                Action: s3:ListBucket
                Resource: !GetAtt SitesBucket.Arn
                Condition:
                  StringLike:
                    s3:prefix: ['worker/*']

  WorkerInstanceProfile:
    Type: AWS::IAM::InstanceProfile
    Condition: UseWorker
    Properties:
      Roles: [!Ref WorkerRole]

  WorkerInstance:
    Type: AWS::EC2::Instance
    Condition: UseWorker
    Properties:
      ImageId: !Ref WorkerAmiId
      InstanceType: !Ref WorkerInstanceType
      IamInstanceProfile: !Ref WorkerInstanceProfile
      SecurityGroupIds: [!GetAtt WorkerSecurityGroup.GroupId]
      InstanceInitiatedShutdownBehavior: stop # the runner shuts the machine down when it is done: that STOPS it (the disk stays), it does not delete it
      BlockDeviceMappings:
        - DeviceName: /dev/xvda
          Ebs:
            VolumeType: gp3
            VolumeSize: !Ref WorkerDiskGb
            Encrypted: true
            DeleteOnTermination: true
      MetadataOptions:
        HttpTokens: required # IMDSv2 only
        HttpPutResponseHopLimit: 1
      Tags:
        - Key: Name
          Value: !Sub '${AWS::StackName}-worker'
      UserData:
        Fn::Base64: !Sub |
          #!/bin/bash
          # First boot only: install the run-on-every-boot service. The service fetches the runner from s3://${SitesBucket}/worker/ (or uses the built-in
          # one), runs it, uploads a report to worker/results/ and shuts the machine down (unless the teacher started it in "keep" mode).
          set -eu
          mkdir -p /opt/worker
          cat > /opt/worker/runner-default.py <<'PYEOF'
          import json, os, platform, shutil, subprocess, sys, time
          print("compute worker: built-in runner")
          print("python", platform.python_version(), platform.machine())
          print("disk free GB", round(shutil.disk_usage("/").free / 1e9, 1))
          print("cpus", os.cpu_count())
          for tool in ("node", "git", "aws"):
              print(tool, shutil.which(tool) or "not installed")
          PYEOF
          cat > /opt/worker/boot.sh <<'SHEOF'
          #!/bin/bash
          BUCKET="${SitesBucket}"
          export AWS_DEFAULT_REGION="${AWS::Region}"
          mkdir -p /opt/worker/work && cd /opt/worker/work
          STARTED=$(date -u +%FT%TZ); STARTED_EPOCH=$(date +%s)
          MODE=$(aws s3 cp "s3://$BUCKET/worker/control/mode.json" - 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get("mode","run"))' 2>/dev/null || echo run)
          aws s3 cp "s3://$BUCKET/worker/runner.py" runner.py >/dev/null 2>&1 || cp /opt/worker/runner-default.py runner.py
          timeout 3300 python3 runner.py > out.txt 2>&1; CODE=$?
          FINISHED=$(date -u +%FT%TZ); FINISHED_EPOCH=$(date +%s)
          OUTPUT=$(tail -c 3000 out.txt | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')
          printf '{"task":"runner","started":"%s","finished":"%s","finished_epoch":%s,"seconds":%s,"exit":%s,"mode":"%s","output":%s}\n' "$STARTED" "$FINISHED" "$FINISHED_EPOCH" "$((FINISHED_EPOCH-STARTED_EPOCH))" "$CODE" "$MODE" "$OUTPUT" > result.json
          aws s3 cp result.json "s3://$BUCKET/worker/results/$STARTED.json" >/dev/null 2>&1
          aws s3 cp result.json "s3://$BUCKET/worker/results/latest.json" >/dev/null 2>&1
          if [ "$MODE" != "keep" ]; then shutdown -h now; fi
          SHEOF
          chmod +x /opt/worker/boot.sh
          cat > /etc/systemd/system/worker-run.service <<'UNITEOF'
          [Unit]
          Description=Class platform worker run
          After=network-online.target
          Wants=network-online.target
          [Service]
          Type=oneshot
          ExecStart=/opt/worker/boot.sh
          [Install]
          WantedBy=multi-user.target
          UNITEOF
          systemctl daemon-reload
          systemctl enable worker-run.service
          systemctl start worker-run.service --no-block

  WorkerControlFunction:
    Type: AWS::Serverless::Function
    Condition: UseWorker
    Properties:
      CodeUri: lambda/
      Handler: worker.handler
      Runtime: python3.12
      Architectures: [arm64]
      MemorySize: 256
      Timeout: 60
      Environment:
        Variables:
          WORKER_INSTANCE_ID: !Ref WorkerInstance
          WORKER_INSTANCE_TYPE: !Ref WorkerInstanceType
          WORKER_DISK_GB: !Ref WorkerDiskGb
          WORKER_MAX_MINUTES: !Ref WorkerMaxMinutes
      Policies:
        - Statement:
            - Effect: Allow # start and stop this ONE machine
              Action: [ec2:StartInstances, ec2:StopInstances]
              Resource: !Sub 'arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:instance/${WorkerInstance}'
            - Effect: Allow # read its state (this call cannot be limited to one machine)
              Action: ec2:DescribeInstances
              Resource: '*'
            - Effect: Allow # its own small records in the tokens table: worker# keys only
              Action: [dynamodb:GetItem, dynamodb:PutItem, dynamodb:UpdateItem, dynamodb:DeleteItem]
              Resource: !GetAtt TokensTable.Arn
              Condition:
                ForAllValues:StringLike:
                  dynamodb:LeadingKeys: ['worker#*']
            - Effect: Allow # the start mode and the last report: worker/ only
              Action: [s3:GetObject, s3:PutObject]
              Resource: !Sub '${SitesBucket.Arn}/worker/*'
      Events:
        Tick:
          Type: Schedule
          Properties:
            Schedule: rate(15 minutes)

  # ---------- CDN ----------
  SitesOAC:''')

# ---- the portal function: environment and permissions
rep('''          DATA_TABLE: !If [UseStorage, !Ref StudentDataTable, '']
      Policies:
        - Statement:
            - Effect: Allow
              Action: [s3:GetObject, s3:PutObject, s3:DeleteObject] # never portal/''', '''          DATA_TABLE: !If [UseStorage, !Ref StudentDataTable, '']
          WORKER_INSTANCE_ID: !If [UseWorker, !Ref WorkerInstance, '']
          WORKER_INSTANCE_TYPE: !If [UseWorker, !Ref WorkerInstanceType, '']
          WORKER_DISK_GB: !If [UseWorker, !Ref WorkerDiskGb, '']
          WORKER_MAX_MINUTES: !If [UseWorker, !Ref WorkerMaxMinutes, '']
      Policies:
        - Statement:
            - !If # compute worker: the teacher's Start and Stop buttons (this one machine) and the page reading its state and last report
              - UseWorker
              - Effect: Allow
                Action: [ec2:StartInstances, ec2:StopInstances]
                Resource: !Sub 'arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:instance/${WorkerInstance}'
              - !Ref AWS::NoValue
            - !If
              - UseWorker
              - Effect: Allow
                Action: ec2:DescribeInstances
                Resource: '*'
              - !Ref AWS::NoValue
            - !If
              - UseWorker
              - Effect: Allow
                Action: [s3:GetObject, s3:PutObject]
                Resource: !Sub '${SitesBucket.Arn}/worker/*'
              - !Ref AWS::NoValue
            - Effect: Allow
              Action: [s3:GetObject, s3:PutObject, s3:DeleteObject] # never portal/''')

# ---- outputs
rep('''  TracesTableName:
    Condition: UseTraces
    Value: !Ref TracesTable''', '''  TracesTableName:
    Condition: UseTraces
    Value: !Ref TracesTable
  WorkerInstanceId:
    Condition: UseWorker
    Description: The compute worker machine (off until the teacher starts it)
    Value: !Ref WorkerInstance''')
open(p, "w", encoding="utf8").write(s)
print("ok")
