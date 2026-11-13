root = r"D:\Github_Codes\class-hosting-platform-github"


def read(p):
    return open(root + "\\" + p.replace("/", "\\"), encoding="utf8").read()


def write(p, s):
    open(root + "\\" + p.replace("/", "\\"), "w", encoding="utf8").write(s)


def sub(p, a, b):
    s = read(p)
    assert a in s, (p, a[:70])
    write(p, s.replace(a, b, 1))


E = "installer/engine.mjs"
sub(E, "// ---------- steps ----------", """// ---------- capacity: how many student requests may run at once ----------
// A new AWS account allows only 10 concurrent function executions, and one student request can hold up to four of them (the gateway, the student's function,
// and the function it calls). So the gateway's caps must follow the account's real limit: too low and a class sees "busy"; too high and the account throttles
// everything. The installer reads the limit and sizes the caps; the template defaults (3 in flight, 10 requests a second) are right for a limit of 10.
export const LAMBDA_QUOTA_CODE = 'L-B99A9384'; // AWS Lambda: concurrent executions
export const CAPACITY_DEFAULTS = { BackendInflightGlobal: 3, BackendInflightPerStudent: 2, BackendApiRate: 10, BackendApiBurst: 20, BackendWaitMs: 0 };
export const CLASS_OF = 30; // the class size the platform is sized and advised for
const CHAIN = 4; // function executions one student request can hold at once

/** The caps for an account whose Lambda concurrency limit is `quota`. The whole limit may be used (the owner's choice); ten stay free for the portal and the daily jobs. */
export function capacityFor(quota) {
  const q = Math.max(1, Math.floor(Number(quota) || 10));
  const inflight = Math.min(250, Math.max(3, Math.floor((q - 10) / CHAIN)));
  const perStudent = Math.min(8, Math.max(2, Math.floor(inflight / 6)));
  const rate = Math.min(500, Math.max(10, inflight * 2));
  return { BackendInflightGlobal: inflight, BackendInflightPerStudent: perStudent, BackendApiRate: rate, BackendApiBurst: rate * 2, BackendWaitMs: inflight >= 20 ? 1500 : 0 };
}

/** The account's Lambda concurrency limit (the applied value, else the AWS default), or null when it cannot be read. Never fails the install. */
export function readLambdaQuota(ctx) {
  for (const op of ['get-service-quota', 'get-aws-default-service-quota']) {
    try {
      const value = Number(String(call(ctx, ['service-quotas', op, '--service-code', 'lambda', '--quota-code', LAMBDA_QUOTA_CODE, '--query', 'Quota.Value', '--output', 'text'])).trim());
      if (Number.isFinite(value) && value > 0) return Math.floor(value);
    } catch { /* try the next, then give up */ }
  }
  return null;
}

/** What to tell the teacher about capacity, in one or two lines. */
export function capacityNote(quota) {
  if (quota === null) return 'Could not read the account\\'s Lambda concurrency limit, so the safe small caps are used (about 3 student requests at once). Check Service Quotas in the AWS console.';
  const inflight = capacityFor(quota).BackendInflightGlobal;
  if (inflight >= CLASS_OF) return null;
  return `This AWS account allows only ${quota} concurrent function executions, so about ${inflight} student requests can run at once: a class of ${CLASS_OF} asking the AI together will often see "busy". Ask AWS for more (free, usually granted within a day): AWS console, Service Quotas, AWS Lambda, "Concurrent executions", request 1000. Then run \\`${'plan'}\\` and apply: the caps follow.`;
}

/** The template parameters that differ from the defaults (an equal value is left out: a stack that already has it keeps it). */
export const capacityParameters = (capacity) => Object.entries(capacity ?? {}).filter(([k, v]) => CAPACITY_DEFAULTS[k] !== v).map(([k, v]) => `${k}=${v}`);

// ---------- steps ----------""")

# parametersFor adds the capacity parameters that differ from the defaults
sub(E, "  if (config.alertParam) p.AlertEmailParameter = config.alertParam;", "  for (const [k, v] of Object.entries(config.capacity ?? {})) if (CAPACITY_DEFAULTS[k] !== v) p[k] = v; // only what differs from the defaults\n  if (config.alertParam) p.AlertEmailParameter = config.alertParam;")
sub(E, "tenant: input.tenant ?? null, alertParam: input.alertParam || '' };", "tenant: input.tenant ?? null, alertParam: input.alertParam || '', capacity: input.capacity ?? null };")

# install: size from the account's quota, say so when it is small
sub(E, "  config.alertParam = config.alertParam || earlier?.alertParam || '';", "  config.alertParam = config.alertParam || earlier?.alertParam || '';\n  const quota = readLambdaQuota(ctx);\n  config.capacity = capacityFor(quota ?? 10);\n  const lowNote = capacityNote(quota);\n  if (lowNote) result.notes = [...(result.notes ?? []), lowNote];")

# plan: the caps follow the quota (an update after a quota increase raises them)
sub(E, "  config.alertParam = live.parameters.AlertEmailParameter || config.alertParam || '';\n", "  config.alertParam = live.parameters.AlertEmailParameter || config.alertParam || '';\n  const quota = readLambdaQuota({ ...ctx, stack: config.stack });\n  config.capacity = capacityFor(quota ?? Number(live.parameters.BackendInflightGlobal ?? 3) * CHAIN + 10); // unreadable: keep what the stack has\n")

# verify: a recommended check for class capacity
sub(E, "  const recommended = [", "  const quotaNow = readLambdaQuota(ctx);\n  const classReady = quotaNow !== null && capacityFor(quotaNow).BackendInflightGlobal >= CLASS_OF;\n  const recommended = [\n    { name: `Capacity: the account allows about ${CLASS_OF} student requests at once (recommended for a class)`, ok: classReady, warn: true, detail: quotaNow === null ? 'the Lambda concurrency limit could not be read' : `the limit is ${quotaNow} concurrent executions (about ${capacityFor(quotaNow).BackendInflightGlobal} requests at once). Request 1000 in Service Quotas, AWS Lambda, Concurrent executions, then run plan and apply` },")
print("ok")
