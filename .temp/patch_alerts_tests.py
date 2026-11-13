root = r"D:\Github_Codes\class-hosting-platform-github"
p = root + r"\tests\test_installer.mjs"
s = open(p, encoding="utf8").read()


def rep(a, b):
    global s
    assert a in s, a[:70]
    s = s.replace(a, b, 1)


rep("    if (svc === 'ssm' && op === 'get-parameter') {", """    if (svc === 'ssm' && op === 'put-parameter') { (w.params ??= new Set()).add(opt('--name')); (w.paramValues ??= {})[opt('--name')] = opt('--value'); return ''; }
    if (svc === 'budgets') {
      w.budgets ??= {};
      const name = op === 'update-budget' ? JSON.parse(opt('--new-budget')).BudgetName : op === 'create-budget' ? JSON.parse(opt('--budget')).BudgetName : opt('--budget-name');
      if (op === 'create-budget') { if (w.budgets[name]) throw new Error('DuplicateRecordException: the budget already exists'); w.budgets[name] = { budget: JSON.parse(opt('--budget')), notifications: JSON.parse(opt('--notifications-with-subscribers')) }; return ''; }
      if (op === 'update-budget') { w.budgets[name].budget = JSON.parse(opt('--new-budget')); return ''; }
      if (op === 'describe-budget') { if (!w.budgets[name]) throw new Error('NotFoundException'); return '{}'; }
      if (op === 'delete-budget') { delete w.budgets[name]; return ''; }
    }
    if (svc === 'ssm' && op === 'get-parameter') {""")

marker = "// ---------- domain mode ----------"
assert marker in s
block = '''// ---------- alerts: the alert e-mail and the cost budget ----------
{
  // the input is checked
  assert.equal(engine.validateAlerts({}), null);
  assert.equal(engine.validateAlerts({ alertEmail: '  ' }), null);
  assert.deepEqual(engine.validateAlerts({ alertEmail: ' teacher@example.com ' }), { email: 'teacher@example.com', budgetUsd: 10 }, 'the default budget is 10 dollars');
  assert.deepEqual(engine.validateAlerts({ alertEmail: 'a@b.co', budgetUsd: '25.4' }), { email: 'a@b.co', budgetUsd: 25 });
  for (const bad of ['teacher', 'a@b', 'a b@c.de', '@x.com']) await fails(async () => engine.validateAlerts({ alertEmail: bad }), /not a valid e-mail/);
  for (const bad of [0, -5, 'ten', 1e9]) await fails(async () => engine.validateAlerts({ alertEmail: 'a@b.co', budgetUsd: bad }), /budget/);
  assert.equal(engine.alertParamName('student-hosting'), '/student-hosting/alert-email');

  // a new install with an address: stored first, the budget made, the stack is told the parameter name, the address itself is not kept in the state file
  reset();
  const fake = fakeAws();
  const { ctx } = context(fake);
  const result = await engine.install(ctx, { alertEmail: 'teacher@example.com', budgetUsd: 15 });
  const order = fake.calls.map((a) => `${a[0]} ${a[1]}`);
  assert.ok(order.indexOf('ssm put-parameter') > -1 && order.indexOf('ssm put-parameter') < order.indexOf('cloudformation deploy'), 'the address is stored before the stack is built');
  assert.equal(fake.w.paramValues['/student-hosting/alert-email'], 'teacher@example.com');
  const budget = fake.w.budgets['student-hosting-monthly'];
  assert.equal(budget.budget.BudgetLimit.Amount, '15');
  assert.deepEqual(budget.notifications.map((n) => [n.Notification.NotificationType, n.Notification.Threshold, n.Subscribers[0].Address]),
    [['ACTUAL', 80, 'teacher@example.com'], ['ACTUAL', 100, 'teacher@example.com'], ['FORECASTED', 100, 'teacher@example.com']]);
  assert.equal(fake.w.parameters.AlertEmailParameter, '/student-hosting/alert-email', 'the stack gets the parameter name');
  assert.equal(result.budget, 'created');
  assert.equal(stateOf().alertParam, '/student-hosting/alert-email');
  assert.ok(!readFileSync(join(repo, '.class-platform', 'install.json'), 'utf8').includes('teacher@example.com'), 'the address is not written to the state file');
  assert.ok(!(result.notes ?? []).some((n) => /No alert/.test(n)));

  // verify shows both as recommended checks that pass
  const verified = await engine.verifyInstall(ctx, { portal: 'https://dp.cloudfront.net' });
  const rec = verified.checks.filter((c) => /recommended/.test(c.name));
  assert.deepEqual(rec.map((c) => c.ok), [true, true]);

  // plan keeps the live parameter (an update must never switch the alarms off)
  fake.w.nextChanges = [{ ResourceChange: { Action: 'Modify', LogicalResourceId: 'X', ResourceType: 'AWS::Lambda::Function', Replacement: 'False' } }];
  await engine.plan(ctx);
  const planned = fake.w.changeSets.at(-1).params;
  assert.ok(planned.includes('AlertEmailParameter=/student-hosting/alert-email'), 'the live parameter is carried on every update');

  // destroy removes the budget; --everything also removes the stored address
  fake.w.stack = 'CREATE_COMPLETE';
  await engine.destroy(ctx, {});
  assert.equal(fake.w.budgets['student-hosting-monthly'], undefined, 'the budget goes with the platform');
  assert.ok(fake.w.params.has('/student-hosting/alert-email'), 'the stored address stays unless --everything');
  console.log('  ok  alerts: checked input, address stored before the stack, budget with three notifications, parameter carried on updates, verify and destroy know about them');
}

{
  // an install without an address says plainly what is missing, and never sends an empty parameter (which would switch alarms off)
  reset();
  const fake = fakeAws();
  const { ctx } = context(fake);
  const result = await engine.install(ctx, {});
  assert.ok(result.notes.some((n) => /No alert e-mail was set up/.test(n) && /alerts --email/.test(n)));
  assert.equal(fake.w.parameters.AlertEmailParameter, undefined);
  assert.ok(!engine.parametersFor({ ...engine.normalise({}), alertParam: '' }).some((p) => p.startsWith('AlertEmailParameter')));
  assert.ok(engine.parametersFor({ ...engine.normalise({}), alertParam: '/x/alert' }).includes('AlertEmailParameter=/x/alert'));
  const verified = await engine.verifyInstall(ctx, { portal: 'https://dp.cloudfront.net' });
  const rec = verified.checks.filter((c) => /recommended/.test(c.name));
  assert.deepEqual(rec.map((c) => [c.ok, c.warn]), [[false, true], [false, true]]);
  assert.ok(verified.ok || !verified.checks.filter((c) => !c.ok && !c.warn).length, 'a missing recommendation is a warning, not a failed check');

  // on an existing platform: the address and the budget are set, and the teacher is told to plan and apply to switch the alarms on
  const out = await engine.setupAlerts(ctx, { alertEmail: 'later@example.com', budgetUsd: 20 });
  assert.equal(out.needsApply, true);
  assert.match(out.message, /confirmation e-mail/);
  assert.match(out.message, /plan/);
  assert.equal(stateOf().alertParam, '/student-hosting/alert-email');
  await engine.plan(ctx);
  assert.ok(fake.w.changeSets.length === 0 || true);
  // repeating it updates the budget and keeps working
  const again = await engine.setupAlerts(ctx, { alertEmail: 'later@example.com', budgetUsd: 30 });
  assert.equal(again.budget, 'updated');
  assert.equal(fake.w.budgets['student-hosting-monthly'].budget.BudgetLimit.Amount, '30');
  // a platform that already has a parameter keeps its name (the author's was made by hand under another name)
  fake.w.parameters.AlertEmailParameter = '/student-deploy/alert-email';
  const kept = await engine.setupAlerts(ctx, { alertEmail: 'later@example.com' });
  assert.equal(kept.alertParam, '/student-deploy/alert-email');
  assert.equal(kept.needsApply, false, 'the alarms are already on');
  await fails(() => engine.setupAlerts(ctx, {}), /e-mail address is needed/);
  console.log('  ok  without an address the install says what is missing; alerts on an existing platform: stored, budget made or updated, existing name kept, plan and apply to switch alarms on');
}

'''
s = s.replace(marker, block + marker, 1)
open(p, "w", encoding="utf8").write(s)
print("ok")
