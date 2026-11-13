p = r"D:\Github_Codes\class-hosting-platform-github\tests\dev_server.py"
s = open(p, encoding="utf8").read()
s = s.replace('TRACES_ENABLED="true", STORAGE_ENABLED="true"', 'TRACES_ENABLED="true", WORKER_INSTANCE_ID="i-0dev", WORKER_INSTANCE_TYPE="c7g.xlarge", WORKER_DISK_GB="30", WORKER_MAX_MINUTES="60", STORAGE_ENABLED="true"', 1)
s = s.replace("import traces  # noqa: E402\n", "import traces  # noqa: E402\nimport worker  # noqa: E402\n", 1)
marker = "\n\nclass Handler"
add = '''

class DevEC2:
    """A pretend machine for the Worker tab: Start makes it 'running' after a few seconds, a run lasts a few seconds, Stop stops it."""

    def __init__(self):
        self.state, self.since = "stopped", 0

    def _tick(self):
        age = time.time() - self.since
        if self.state == "pending" and age > 3:
            self.state, self.since = "running", time.time()
        elif self.state == "running" and self.since and age > 12 and self.auto:
            self.state = "stopped"

    auto = True

    def describe_instances(self, InstanceIds):
        self._tick()
        return {"Reservations": [{"Instances": [{"State": {"Name": self.state}, "LaunchTime": None, "InstanceType": "c7g.xlarge"}]}]}

    def start_instances(self, InstanceIds):
        self.state, self.since = "pending", time.time()

    def stop_instances(self, InstanceIds):
        self.state = "stopped"


class DevWorkerS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        import io
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.objects[Key])}


_dev_s3 = DevWorkerS3()
_dev_s3.objects[worker.RESULTS_KEY] = json.dumps({"started": "2026-11-06T03:00:02Z", "finished_epoch": t0 - 3000, "seconds": 41, "exit": 0, "task": "runner",
                                                  "output": "compute worker: built-in runner\\npython 3.9.16 aarch64\\ndisk free GB 21.4\\ncpus 4"}).encode()
worker._clients.update({"ec2": DevEC2(), "s3": _dev_s3})
'''
assert marker in s
s = s.replace(marker, add + marker, 1)
open(p, "w", encoding="utf8").write(s)
print("ok")
