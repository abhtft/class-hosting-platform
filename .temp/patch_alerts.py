root = r"D:\Github_Codes\class-hosting-platform-github"


def edit(p, a, b):
    s = open(root + "\\" + p, encoding="utf8").read()
    assert a in s, (p, a[:70])
    open(root + "\\" + p, "w", encoding="utf8").write(s.replace(a, b, 1))


E = "installer/engine.mjs"

# ---- helpers (before the "steps" section)
edit(E, "// ---------- steps ----------", """// ---------- alerts: the alert email and the cost budget ----------
// The platform can email one address about crashes, throttling, errors and (with traces) a jump in refusals; the address lives in an SSM String parameter
// whose NAME is the stack parameter AlertEmailParameter. The installer asks for the address, stores it, passes the name, and creates an AWS Budget that
// emails the same address, so a new teacher is told when something is wrong or the bill grows. Both are optional but recommended.
export const DEFAULT_BUDGET_USD = 10;
export const alertParamName = (stack) => `/${stack}/alert-email`;
export const budgetName = (stack) => `${stack}-monthly`;
const EMAIL_RE = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]{2,}$/;

/** { email, budgetUsd } from the teacher's input, or null when no alerts were asked for. Throws a clear error for a bad address or amount. */
export function validateAlerts(input = {}) {
  const email = String(input.alertEmail ?? '').trim();
  if (!email) return null;
  if (!EMAIL_RE.test(email) || email.length > 120) throw new InstallError(`"${email}" is not a valid e-mail address.`, 'Use an address you read, for example you@example.com.');
  const raw = input.budgetUsd ?? DEFAULT_BUDGET_USD;
  const budgetUsd = Number(raw);
  if (!Number.isFinite(budgetUsd) || budgetUsd < 1 || budgetUsd > 100000) throw new InstallError(`The monthly budget "${raw}" is not valid.`, 'Use a whole number of US dollars from 1 to 100000, for example 10.');
  return { email, budgetUsd: Math.round(budgetUsd) };
}

/** Store the address in SSM (overwrites: safe to repeat) and return the parameter name the stack needs. A platform that already has one keeps its name. */
export function putAlertEmail(ctx, email, existingParam = '') {
  const name = existingParam || alertParamName(ctx.stack);
  call(ctx, ['ssm', 'put-parameter', '--name', name, '--type', 'String', '--value', email, '--overwrite']);
  return name;
}

/** A monthly cost budget that emails at 80 percent and 100 percent of the amount (actual) and when 100 percent is forecast. Repeating it updates the amount. */
export function ensureBudget(ctx, account, { email, budgetUsd }) {
  const budget = { BudgetName: budgetName(ctx.stack), BudgetLimit: { Amount: String(budgetUsd), Unit: 'USD' }, TimeUnit: 'MONTHLY', BudgetType: 'COST' };
  const to = [{ SubscriptionType: 'EMAIL', Address: email }];
  const notes = [['ACTUAL', 80], ['ACTUAL', 100], ['FORECASTED', 100]].map(([NotificationType, Threshold]) => ({
    Notification: { NotificationType, ComparisonOperator: 'GREATER_THAN', Threshold, ThresholdType: 'PERCENTAGE' }, Subscribers: to }));
  try {
    call(ctx, ['budgets', 'create-budget', '--account-id', account, '--budget', JSON.stringify(budget), '--notifications-with-subscribers', JSON.stringify(notes)]);
    return 'created';
  } catch (e) {
    if (!/Duplicate|already exists/i.test(e.message)) throw e;
    call(ctx, ['budgets', 'update-budget', '--account-id', account, '--new-budget', JSON.stringify(budget)]);
    return 'updated';
  }
}

/** Set up the alert email and the budget on an EXISTING platform (the install does it for a new one). The alarms themselves are part of the stack: run `plan` and `apply` afterwards. */
export async function setupAlerts(ctx, input) {
  const alerts = validateAlerts(input);
  if (!alerts) throw new InstallError('An e-mail address is needed.', `Run: ${cli(ctx)} alerts --email you@example.com [--budget 10]`);
  const found = preflight(ctx);
  const live = found.stackStatus ? stackOutputs(ctx).parameters : {};
  const param = putAlertEmail(ctx, alerts.email, live.AlertEmailParameter || '');
  const budget = ensureBudget(ctx, found.account, alerts);
  const state = readState(ctx);
  if (state && !ctx.dryRun) writeState(ctx, { ...state, alertParam: param });
  const needsApply = found.stackStatus && !live.AlertEmailParameter;
  return { ok: true, alertParam: param, budget, needsApply,
    message: `Alert address saved and a monthly budget of $${alerts.budgetUsd} ${budget} (it emails at 80% and 100% of the amount, and when 100% is forecast). AWS sends each address a confirmation e-mail: open it and confirm, or nothing is delivered.${needsApply ? ` To switch the error alarms on, run \\`${cli(ctx)} plan\\`, read it, then \\`${cli(ctx)} apply <plan id>\\`.` : ''}` };
}

// ---------- steps ----------""")

# ---- normalise keeps the stored parameter name; parametersFor passes it only when set
edit(E, "updateNotices: input.updateNotices !== false, tenant: input.tenant ?? null };", "updateNotices: input.updateNotices !== false, tenant: input.tenant ?? null, alertParam: input.alertParam || '' };")
edit(E, "  p.RoutingEndpoint = config.tenant?.routingEndpoint ?? '';\n  return Object.entries(p)", "  p.RoutingEndpoint = config.tenant?.routingEndpoint ?? '';\n  if (config.alertParam) p.AlertEmailParameter = config.alertParam; // only when set: a stack that has one keeps it, and an empty value would switch the alarms off\n  return Object.entries(p)")

# ---- install: alerts first (the parameter must exist before the stack is built)
edit(E, """  const config = normalise({ ...input, profile: ctx.profile, stack: ctx.stack });
  let found = preflight(ctx);""", """  const config = normalise({ ...input, profile: ctx.profile, stack: ctx.stack });
  const alerts = validateAlerts(input);
  let found = preflight(ctx);""")
edit(E, """  if (config.domain) {
    const cert = await ensureCertificate(ctx, config.domain);""", """  config.alertParam = config.alertParam || earlier?.alertParam || '';
  if (alerts) { // before the stack: it reads the address while it creates the alarms' subscription
    config.alertParam = putAlertEmail(ctx, alerts.email, config.alertParam);
    result.budget = ensureBudget(ctx, found.account, alerts);
    say(`Alert e-mail saved, and a monthly budget of $${alerts.budgetUsd} created. AWS sends a confirmation e-mail to ${alerts.email}: open it and confirm.`);
  } else if (!config.alertParam) {
    result.notes = [...(result.notes ?? []), `No alert e-mail was set up, so nobody will be told about errors or a growing bill. Add one any time:  ${cli(ctx)} alerts --email you@example.com`];
  }
  if (config.domain) {
    const cert = await ensureCertificate(ctx, config.domain);""")

# ---- plan: the live stack is the truth for the alert parameter
edit(E, "  config.tenant = tenantFromParameters(live.parameters); // the live stack is the truth: an update must never clear the ids it already has\n", "  config.tenant = tenantFromParameters(live.parameters); // the live stack is the truth: an update must never clear the ids it already has\n  config.alertParam = live.parameters.AlertEmailParameter || config.alertParam || '';\n")

# ---- verify: two recommended-but-not-required checks
edit(E, "  return { ...result, portalUrl: url };\n}", """  const alertSet = Boolean(found.parameters.AlertEmailParameter);
  let budgetSet = false;
  try { call(ctx, ['budgets', 'describe-budget', '--account-id', call(ctx, ['sts', 'get-caller-identity'], { json: true }).Account, '--budget-name', budgetName(ctx.stack)]); budgetSet = true; } catch { /* none, or not allowed to read it */ }
  const recommended = [
    { name: 'Alerts: an alert e-mail is set up (recommended)', ok: alertSet, warn: true, detail: `not set up: nobody is told about errors. Run: ${cli(ctx)} alerts --email you@example.com` },
    { name: 'Cost: a monthly budget with an e-mail alert exists (recommended)', ok: budgetSet, warn: true, detail: `not found. Run: ${cli(ctx)} alerts --email you@example.com` },
  ];
  return { ...result, checks: [...result.checks, ...recommended], portalUrl: url };
}""")

# ---- destroy: the budget goes with the platform; the alert address with --everything
edit(E, "  const staging = `${ctx.stack.toLowerCase()}-artifacts-${account}`;", """  try { call(ctx, ['budgets', 'delete-budget', '--account-id', account, '--budget-name', budgetName(ctx.stack)]); } catch { /* there was none */ }
  const alertParam = readState(ctx)?.alertParam || alertParamName(ctx.stack);
  if (everything && exists(ctx, ['ssm', 'get-parameter', '--name', alertParam])) call(ctx, ['ssm', 'delete-parameter', '--name', alertParam]);
  const staging = `${ctx.stack.toLowerCase()}-artifacts-${account}`;""")

# ---- verify.mjs: a warning does not fail the check
V = "installer/verify.mjs"
edit(V, "  const failed = checks.filter((c) => !c.ok && !(publicOnly && c.name === 'Signed-in checks'));", "  const failed = checks.filter((c) => !c.ok && !c.warn && !(publicOnly && c.name === 'Signed-in checks'));")

# ---- setup.mjs: flags, wizard questions, the alerts command, printing
S = "setup.mjs"
edit(S, "c.name ? `  ${c.ok ? 'ok  ' : 'FAIL'} ${c.name}", "c.name ? `  ${c.ok ? 'ok  ' : c.warn ? 'WARN' : 'FAIL'} ${c.name}")
edit(S, "  answers.teacher = await ask('Choose your teacher login name (3-40 letters, digits . _ -):', 'teacher');", """  if (flags['no-alerts']) answers.alertEmail = '';
  else if (flags['alert-email'] !== undefined) { answers.alertEmail = flags['alert-email']; answers.budgetUsd = flags.budget; }
  else {
    console.log('');
    console.log('Alerts (recommended): an e-mail when something breaks, and when the AWS bill passes a monthly budget. AWS sends a confirmation mail you must open.');
    answers.alertEmail = await ask('E-mail address for alerts (Enter to skip):', '');
    if (answers.alertEmail) answers.budgetUsd = await ask('Monthly budget in US dollars:', String(engine.DEFAULT_BUDGET_USD));
  }
  answers.teacher = await ask('Choose your teacher login name (3-40 letters, digits . _ -):', 'teacher');""")
edit(S, "return finish(await engine.install(ctx, { domain: flags.domain, features: flags.features === undefined ? undefined : parseFeatures(flags.features), certificateArn: flags['certificate-arn'] }));",
     "return finish(await engine.install(ctx, { domain: flags.domain, features: flags.features === undefined ? undefined : parseFeatures(flags.features), certificateArn: flags['certificate-arn'], alertEmail: flags['no-alerts'] ? '' : flags['alert-email'], budgetUsd: flags.budget }));")
edit(S, "      case 'finish-domain': return finish(await engine.finishDomain(ctx));", """      case 'finish-domain': return finish(await engine.finishDomain(ctx));
      case 'alerts': {
        let email = flags.email ?? flags['alert-email'];
        if (!email && !flags.json && process.stdin.isTTY) email = await prompt.ask('E-mail address for alerts:');
        return finish(await engine.setupAlerts(ctx, { alertEmail: email, budgetUsd: flags.budget }));
      }""")
edit(S, "//   node setup.mjs finish-domain        step two of a domain install, after the DNS records are added", "//   node setup.mjs finish-domain        step two of a domain install, after the DNS records are added\n//   node setup.mjs alerts --email a@b.c [--budget 10]   alert e-mail and a monthly cost budget (the install asks for them; use this on an existing platform, then plan and apply)")
print("ok")
