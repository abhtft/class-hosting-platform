root = r"D:\Github_Codes\class-hosting-platform-github"


def edit(p, a, b, count=1):
    s = open(root + "\\" + p, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(root + "\\" + p, "w", encoding="utf8").write(s.replace(a, b, count))


# ---- template
edit("template.yaml", """    Default: c7g.xlarge
    AllowedValues: [c7g.large, c7g.xlarge, c7g.2xlarge, t4g.medium, t4g.large]
    Description: The machine size (ARM, like the platform's functions). c7g.xlarge has 4 vCPUs and 8 GB of memory.""",
     """    Default: m7i-flex.large
    AllowedValues: [m7i-flex.large, c7i-flex.large, t3.small, t4g.small, c7g.large, c7g.xlarge, c7g.2xlarge]
    Description: >
      The machine size. The default (2 vCPUs, 8 GB, x86) is one a Free plan account may use; the c7g sizes (ARM) need a paid-plan account. The
      installer picks the matching image (x86 or ARM) once.""")
edit("template.yaml", "    Description: Set by the installer (the current Amazon Linux 2023 ARM image, chosen once so a later image release never replaces the machine). Leave empty.",
     "    Description: Set by the installer (the current Amazon Linux 2023 image for the machine's architecture, chosen once so a later image release never replaces the machine). Leave empty.")

# ---- installer: the image follows the machine's architecture
edit("installer/engine.mjs", """export const WORKER_AMI_PARAMETER = '/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64';""",
     """export const WORKER_DEFAULT_TYPE = 'm7i-flex.large'; // a type a Free plan account may use (2 vCPUs, 8 GB, x86); keep in step with the WorkerInstanceType default in template.yaml
const WORKER_ARM = /^(a1|c6g|c7g|c8g|m6g|m7g|m8g|r6g|r7g|t4g)\\./;
export const workerAmiParameter = (type = WORKER_DEFAULT_TYPE) => `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-${WORKER_ARM.test(type) ? 'arm64' : 'x86_64'}`;""")
edit("installer/engine.mjs", """  if (!ami) ami = String(call(ctx, ['ssm', 'get-parameter', '--name', WORKER_AMI_PARAMETER, '--query', 'Parameter.Value', '--output', 'text'])).trim();""",
     """  const type = params.find((p) => p.startsWith('WorkerInstanceType='))?.split('=')[1] || WORKER_DEFAULT_TYPE;
  if (!ami) ami = String(call(ctx, ['ssm', 'get-parameter', '--name', workerAmiParameter(type), '--query', 'Parameter.Value', '--output', 'text'])).trim();""")
edit("installer/engine.mjs", "(the stack's own value when it has one, else the current Amazon Linux 2023 ARM image)", "(the stack's own value when it has one, else the current Amazon Linux 2023 image for the machine's architecture)")

# ---- worker.py prices (estimates)
edit("lambda/worker.py", """PRICES = {"c7g.large": 0.0725, "c7g.xlarge": 0.145, "c7g.2xlarge": 0.29, "t4g.medium": 0.0336, "t4g.large": 0.0672}""",
     """PRICES = {"m7i-flex.large": 0.0958, "c7i-flex.large": 0.0848, "t3.small": 0.0208, "t4g.small": 0.0168, "c7g.large": 0.0725, "c7g.xlarge": 0.145, "c7g.2xlarge": 0.29}""")

# ---- tests
edit("tests/test_template_worker.py", """assert re.search(r"  WorkerInstanceType:\\n    Type: String\\n    Default: c7g.xlarge\\n    AllowedValues: \\[c7g.large, c7g.xlarge, c7g.2xlarge, t4g.medium, t4g.large\\]", params)""",
     """assert re.search(r"  WorkerInstanceType:\\n    Type: String\\n    Default: m7i-flex.large\\n    AllowedValues: \\[m7i-flex.large, c7i-flex.large, t3.small, t4g.small, c7g.large, c7g.xlarge, c7g.2xlarge\\]", params), "the default is a type a Free plan account may use\"""")
edit("tests/test_installer.mjs", """  assert.ok(asked.some((a) => a.startsWith('ssm get-parameter') && a.includes(engine.WORKER_AMI_PARAMETER)));""",
     """  assert.ok(asked.some((a) => a.startsWith('ssm get-parameter') && a.includes('al2023-ami-kernel-default-x86_64')), 'the default machine is x86, so is its image');
  assert.equal(engine.workerAmiParameter('c7g.xlarge').endsWith('arm64'), true);
  assert.equal(engine.workerAmiParameter('t4g.small').endsWith('arm64'), true);
  assert.equal(engine.workerAmiParameter('m7i-flex.large').endsWith('x86_64'), true);
  const arm = [];
  engine.deployArgs({ ...ctx, aws: (args) => { arm.push(args.join(' ')); if (args[0] === 'cloudformation') throw new Error('no stack'); return 'ami-arm\\n'; } }, '/tmp/t.yaml', ['WorkerEnabled=true', 'WorkerInstanceType=c7g.xlarge']);
  assert.ok(arm.some((a) => a.includes('al2023-ami-kernel-default-arm64')), 'an ARM machine gets the ARM image');""")
print("ok")
