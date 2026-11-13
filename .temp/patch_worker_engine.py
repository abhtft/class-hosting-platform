p = r"D:\Github_Codes\class-hosting-platform-github\installer\engine.mjs"
s = open(p, encoding="utf8").read()


def rep(a, b):
    global s
    assert a in s, a[:70]
    s = s.replace(a, b, 1)


rep("knowledge: 'KnowledgeEnabled', traces: 'TracesEnabled' };", "knowledge: 'KnowledgeEnabled', traces: 'TracesEnabled', worker: 'WorkerEnabled' };")
rep("export const ALL_FEATURES = ['backend', 'ai', 'gemini', 'storage', 'knowledge', 'traces', 'customdomains'];",
    "export const ALL_FEATURES = ['backend', 'ai', 'gemini', 'storage', 'knowledge', 'traces', 'customdomains']; // the compute worker (a machine that costs money while on) is never part of \"everything\": it is asked for with --enable worker")
rep("export const deployArgs = (ctx, packaged, params, extra = []) => ['cloudformation', 'deploy', '--template-file', packaged, '--stack-name', ctx.stack, '--capabilities', 'CAPABILITY_IAM', 'CAPABILITY_AUTO_EXPAND',\n  '--no-fail-on-empty-changeset', ...(packagedWith.has(packaged) ? ['--s3-bucket', packagedWith.get(packaged)] : []), '--parameter-overrides', ...params, ...extra];",
    """/** The machine image of the compute worker is chosen ONCE (the stack's own value when it has one, else the current Amazon Linux 2023 ARM image) and then carried on
 *  every update, so a new image release never makes CloudFormation replace the machine. Nothing is added while the worker is off. */
export const WORKER_AMI_PARAMETER = '/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64';
function withWorkerImage(ctx, params) {
  if (!params.includes('WorkerEnabled=true') || params.some((p) => p.startsWith('WorkerAmiId='))) return params;
  let ami = '';
  try { ami = stackOutputs(ctx).parameters.WorkerAmiId || ''; } catch { /* a first install has no stack yet */ }
  if (!ami) ami = String(call(ctx, ['ssm', 'get-parameter', '--name', WORKER_AMI_PARAMETER, '--query', 'Parameter.Value', '--output', 'text'])).trim();
  return ami ? [...params, `WorkerAmiId=${ami}`] : params;
}

export const deployArgs = (ctx, packaged, params, extra = []) => ['cloudformation', 'deploy', '--template-file', packaged, '--stack-name', ctx.stack, '--capabilities', 'CAPABILITY_IAM', 'CAPABILITY_AUTO_EXPAND',
  '--no-fail-on-empty-changeset', ...(packagedWith.has(packaged) ? ['--s3-bucket', packagedWith.get(packaged)] : []), '--parameter-overrides', ...withWorkerImage(ctx, params), ...extra];""")
open(p, "w", encoding="utf8").write(s)
print("ok")
