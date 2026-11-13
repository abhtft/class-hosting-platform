p = r"D:\Github_Codes\class-hosting-platform-github\tests\test_installer.mjs"
s = open(p, encoding="utf8").read()


def rep(a, b, count=1):
    global s
    assert a in s, a[:70]
    s = s.replace(a, b, count)


rep("    if (svc === 'ssm' && op === 'put-parameter') {", """    if (svc === 'service-quotas') {
      if (op === 'get-service-quota' && w.quotaApplied !== undefined) { if (w.quotaApplied === null) throw new Error('NoSuchResourceException'); return `${w.quotaApplied}\\n`; }
      if (w.quota === undefined) throw new Error('AccessDenied');
      return `${w.quota}.0\\n`;
    }
    if (svc === 'ssm' && op === 'put-parameter') {""")
rep("const rec = verified.checks.filter((c) => /recommended/.test(c.name));\n  assert.deepEqual(rec.map((c) => c.ok), [true, true]);", "const rec = verified.checks.filter((c) => /^(Alerts|Cost)/.test(c.name));\n  assert.deepEqual(rec.map((c) => c.ok), [true, true]);")
rep("const rec = verified.checks.filter((c) => /recommended/.test(c.name));\n  assert.deepEqual(rec.map((c) => [c.ok, c.warn]), [[false, true], [false, true]]);", "const rec = verified.checks.filter((c) => /^(Alerts|Cost)/.test(c.name));\n  assert.deepEqual(rec.map((c) => [c.ok, c.warn]), [[false, true], [false, true]]);")

marker = "// ---------- domain mode ----------"
block = '''// ---------- capacity: the caps follow the account's Lambda concurrency limit ----------
{
  assert.deepEqual(engine.capacityFor(10), engine.CAPACITY_DEFAULTS, 'a new account (10) keeps today\\'s caps');
  assert.deepEqual(engine.capacityFor(null), engine.CAPACITY_DEFAULTS, 'an unreadable limit is treated as the smallest');
  assert.equal(engine.capacityFor(5).BackendInflightGlobal, 3, 'never below 3');
  const big = engine.capacityFor(1000);
  assert.deepEqual(big, { BackendInflightGlobal: 247, BackendInflightPerStudent: 8, BackendApiRate: 494, BackendApiBurst: 988, BackendWaitMs: 1500 }, 'the whole limit is used, ten kept for the portal');
  assert.equal(engine.capacityFor(100000).BackendInflightGlobal, 250, 'capped');
  assert.equal(engine.capacityFor(130).BackendInflightGlobal, 30, 'a class of 30 needs a limit of about 130');
  assert.equal(engine.capacityFor(120).BackendWaitMs, 1500, 'waiting is on once there is room for it');
  assert.equal(engine.capacityFor(50).BackendWaitMs, 1500);
  assert.equal(engine.capacityFor(40).BackendWaitMs, 0, 'but not on a tiny account: a waiting gateway holds an execution itself');
  assert.equal(engine.capacityNote(1000), null);
  assert.match(engine.capacityNote(10), /only 10 concurrent function executions/);
  assert.match(engine.capacityNote(10), /Service Quotas/);
  assert.match(engine.capacityNote(null), /Could not read/);
  assert.deepEqual(engine.capacityParameters(engine.CAPACITY_DEFAULTS), [], 'values equal to the defaults are not sent');
  assert.deepEqual(engine.capacityParameters(big).sort(), ['BackendApiBurst=988', 'BackendApiRate=494', 'BackendInflightGlobal=247', 'BackendInflightPerStudent=8', 'BackendWaitMs=1500']);

  // reading the limit: the applied value, else the AWS default, else nothing (and never an error)
  assert.equal(engine.readLambdaQuota(context(fakeAws({ quota: 1000 })).ctx), 1000);
  assert.equal(engine.readLambdaQuota(context(fakeAws({ quotaApplied: null, quota: 10 })).ctx), 10, 'a new account has no applied value yet: the default is used');
  assert.equal(engine.readLambdaQuota(context(fakeAws({})).ctx), null);

  // a new install on a big account sends the caps; on a new account it sends none and says what to do
  reset();
  let fake = fakeAws({ quota: 1000 });
  let result = await engine.install(context(fake).ctx, {});
  assert.equal(fake.w.parameters.BackendInflightGlobal, '247');
  assert.equal(fake.w.parameters.BackendWaitMs, '1500');
  assert.ok(!(result.notes ?? []).some((n) => /concurrent function executions/.test(n)), 'a big account gets no warning');
  reset();
  fake = fakeAws({ quota: 10 });
  result = await engine.install(context(fake).ctx, {});
  assert.equal(fake.w.parameters.BackendInflightGlobal, undefined, 'the defaults are not sent');
  assert.ok(result.notes.some((n) => /only 10 concurrent function executions/.test(n) && /request 1000/.test(n)), 'a new account is told how to ask for more');

  // the account's limit is raised later: plan lists the new caps; verify warns until then
  const now = await engine.verifyInstall({ ...context(fake).ctx, fetch: async () => ({ status: 401, json: async () => null, text: async () => '', arrayBuffer: async () => new ArrayBuffer(0), headers: { get: () => null } }) }, { portal: 'https://dp.cloudfront.net' });
  const cap = now.checks.find((c) => /^Capacity/.test(c.name));
  assert.deepEqual([cap.ok, cap.warn], [false, true]);
  fake.w.quota = 1000;
  fake.w.nextChanges = [{ ResourceChange: { Action: 'Modify', LogicalResourceId: 'GatewayFunction', ResourceType: 'AWS::Lambda::Function', Replacement: 'False' } }];
  await engine.plan(context(fake).ctx);
  const planned = fake.w.changeSets.at(-1).params;
  assert.ok(planned.includes('BackendInflightGlobal=247') && planned.includes('BackendApiRate=494'), 'the update carries the new caps');
  const after = await engine.verifyInstall({ ...context(fake).ctx, fetch: async () => ({ status: 401, json: async () => null, text: async () => '', arrayBuffer: async () => new ArrayBuffer(0), headers: { get: () => null } }) }, { portal: 'https://dp.cloudfront.net' });
  assert.equal(after.checks.find((c) => /^Capacity/.test(c.name)).ok, true);
  console.log('  ok  capacity: caps follow the Lambda limit (the whole limit, ten kept free), waiting only where there is room, a new account is told how to ask for more, plan raises the caps after an increase, verify warns');
}

'''
s = s.replace(marker, block + marker, 1)
open(p, "w", encoding="utf8").write(s)
print("ok")
