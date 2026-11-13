root = r"D:\Github_Codes\class-hosting-platform-github"


def edit(p, a, b):
    s = open(root + "\\" + p, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(root + "\\" + p, "w", encoding="utf8").write(s.replace(a, b, 1))


edit("tests/dev_server.py", """    def get_object(self, Bucket, Key):
        import io
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.objects[Key])}
""", """    def get_object(self, Bucket, Key):
        import io
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def generate_presigned_post(self, Bucket, Key, Fields, Conditions, ExpiresIn):
        self.objects[Key] = b"PK" + b"0" * 2000  # the demo pretends the upload already happened
        return {"url": "http://127.0.0.1/upload", "fields": {**Fields, "key": Key}}

    def head_object(self, Bucket, Key):
        return {"ContentLength": len(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return "http://127.0.0.1/download/" + Params["Key"]
""")
edit("tests/run.mjs", "['test_worker.py', 'test_template_worker.py', 'test_worker_runner.py']", "['test_worker.py', 'test_worker_jobs.py', 'test_template_worker.py', 'test_worker_runner.py']")
print("ok")
