root = r"D:\Github_Codes\class-hosting-platform-github"


def edit(p, a, b):
    s = open(root + "\\" + p, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(root + "\\" + p, "w", encoding="utf8").write(s.replace(a, b, 1))


edit("build-teacher-package.mjs", "const FOLDERS = ['lambda', 'web', 'starter', 'student', 'installer', 'custom-domain'];", "const FOLDERS = ['lambda', 'web', 'starter', 'student', 'installer', 'custom-domain', 'worker'];")
edit("tests/test_teacher_package.mjs", "'installer/engine.mjs', 'mcp/server.mjs',", "'installer/engine.mjs', 'worker/runner.py', 'mcp/server.mjs',")
edit("admin.mjs", """  aws('cloudfront', 'create-invalidation', '--distribution-id', outputs.PortalDistributionId, '--paths', '/*');
  rmSync(kitTemp, { recursive: true, force: true });""", """  aws('cloudfront', 'create-invalidation', '--distribution-id', outputs.PortalDistributionId, '--paths', '/*');
  rmSync(kitTemp, { recursive: true, force: true });
  // The compute worker's runner (platform-owned code; the machine downloads it at every start). Only when the stack has the worker.
  if (outputs.WorkerInstanceId) aws('s3', 'cp', join(root, 'worker', 'runner.py'), `s3://${outputs.BucketName}/worker/runner.py`, '--content-type', 'text/x-python', '--cache-control', 'no-cache');""")
edit("tests/run.mjs", "for (const test of ['test_worker.py', 'test_template_worker.py'])", "for (const test of ['test_worker.py', 'test_template_worker.py', 'test_worker_runner.py'])")
print("ok")
