"""In-memory stand-ins for S3 and CloudFront shared by the local tests."""
import io
import json
import os
import re
import time

from botocore.exceptions import ClientError

store, invalidations = {}, []


class Body:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data


class FakeS3:
    def __init__(self, port=0):
        self.port = port
        self.posts = []

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"http://127.0.0.1:{self.port}/s3/{Params['Key']}"

    def generate_presigned_post(self, Bucket, Key, Fields, Conditions, ExpiresIn):
        self.posts.append({"Key": Key, "Fields": Fields, "Conditions": Conditions, "ExpiresIn": ExpiresIn})
        return {"url": f"http://127.0.0.1:{self.port}/s3-post", "fields": {**Fields, "key": Key, "policy": "fake-policy", "x-amz-signature": "fake"}}

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000, ContinuationToken=None):
        keys = [k for k in sorted(store) if k.startswith(Prefix)]
        return {"Contents": [{"Key": k, "Size": len(store[k]["Body"]), "LastModified": 1700000000} for k in keys]}

    def head_object(self, Bucket, Key):
        if Key not in store:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(store[Key]["Body"])}

    def get_object(self, Bucket, Key):
        return {"Body": Body(store[Key]["Body"])}

    def put_object(self, Bucket, Key, Body, **kw):
        store[Key] = {"Body": Body, **kw}

    def copy_object(self, Bucket, Key, CopySource):
        store[Key] = dict(store[CopySource["Key"]])

    def delete_object(self, Bucket, Key):
        store.pop(Key, None)

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            store.pop(o["Key"], None)

    def get_paginator(self, name):
        class Paginator:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in sorted(store) if k.startswith(Prefix)]}

        return Paginator()


class FakeSSM:
    def get_parameter(self, Name):
        return {"Parameter": {"Value": json.dumps({"alice": "s3cret"})}}


class FakeCloudFront:
    def create_invalidation(self, DistributionId, InvalidationBatch):
        invalidations.append(InvalidationBatch["Paths"]["Items"])


class FakeTenantCloudFront:
    """In-memory CloudFront multi-tenant API (distribution tenants) for the custom-domain tests and the dev server.

    Tests drive each step with the flags below; dev_server.py subclasses it to advance them with timers.
    """

    def __init__(self):
        self.dns_ok = False  # True, or a callable(domain) -> bool: the CNAME is visible to CloudFront
        self.cert_status = "pending-validation"
        self.deployed = True
        self.tenants = {}
        self.invalidations = []
        self.main_invalidations = []
        self.refuse_with = None  # (code, message): make create_distribution_tenant fail
        self._n = 0

    @staticmethod
    def _error(code, message="", op="Call"):
        return ClientError({"Error": {"Code": code, "Message": message}}, op)

    def _dns_ok(self, domain):
        return self.dns_ok(domain) if callable(self.dns_ok) else self.dns_ok

    def create_distribution_tenant(self, **kw):
        if self.refuse_with:
            raise self._error(*self.refuse_with)
        domain = kw["Domains"][0]["Domain"]
        if not self._dns_ok(domain):
            raise self._error("InvalidArgument", "Could not verify Domain Name ownership", "CreateDistributionTenant")
        assert kw["ManagedCertificateRequest"]["PrimaryDomainName"] == domain
        self._n += 1
        tenant = {"Id": f"dt_{self._n}", "Name": kw["Name"], "Enabled": True, "Parameters": kw["Parameters"],
                  "Domains": [{"Domain": domain, "Status": "inactive"}], "Customizations": {}}
        self.tenants[tenant["Id"]] = tenant
        return {"DistributionTenant": tenant}

    def _tenant(self, tenant_id):
        if tenant_id not in self.tenants:
            raise self._error("EntityNotFound", "", "GetDistributionTenant")
        return self.tenants[tenant_id]

    def get_distribution_tenant(self, Identifier):
        tenant = self._tenant(Identifier)
        return {"DistributionTenant": dict(tenant, Status="Deployed" if self.deployed else "InProgress"), "ETag": "e"}

    def get_managed_certificate_details(self, Identifier):
        self._tenant(Identifier)
        return {"ManagedCertificateDetails": {"CertificateArn": "arn:aws:acm:us-east-1:1:certificate/x", "CertificateStatus": self.cert_status}}

    def update_distribution_tenant(self, Id, IfMatch, Domains, **kw):
        tenant = self._tenant(Id)
        if "Customizations" in kw:
            tenant["Customizations"] = kw["Customizations"]
            tenant["Domains"][0]["Status"] = "active"
        if "Enabled" in kw:
            tenant["Enabled"] = kw["Enabled"]

    def delete_distribution_tenant(self, Id, IfMatch):
        tenant = self._tenant(Id)
        assert not tenant["Enabled"], "a tenant must be disabled before it is deleted"
        del self.tenants[Id]

    def create_invalidation(self, DistributionId, InvalidationBatch):
        self.main_invalidations.append(InvalidationBatch["Paths"]["Items"])

    def create_invalidation_for_distribution_tenant(self, Id, InvalidationBatch):
        self._tenant(Id)
        self.invalidations.append((Id, InvalidationBatch["Paths"]["Items"]))


class FakeLogs:
    """CloudWatch Logs: just enough for log groups, retention and filter_log_events."""

    def __init__(self):
        self.groups, self.retention = {}, {}

    def create_log_group(self, logGroupName):
        if logGroupName in self.groups:
            raise ClientError({"Error": {"Code": "ResourceAlreadyExistsException"}}, "CreateLogGroup")
        self.groups[logGroupName] = []

    def put_retention_policy(self, logGroupName, retentionInDays):
        self.retention[logGroupName] = retentionInDays

    def delete_log_group(self, logGroupName):
        if logGroupName not in self.groups:
            raise ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "DeleteLogGroup")
        del self.groups[logGroupName]

    def add(self, group, message):
        self.groups.setdefault(group, []).append({"timestamp": int(time.time() * 1000), "message": message})

    def filter_log_events(self, logGroupName, limit=50, startTime=0):
        if logGroupName not in self.groups:
            raise ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "FilterLogEvents")
        return {"events": [e for e in self.groups[logGroupName] if e["timestamp"] >= startTime][-limit:]}


class FakeLambda:
    """Lambda control plane + invoke. Python and Node handlers from the uploaded zip are REALLY executed, so the
    student contract (payload v2 event in, {statusCode, headers, body} out) is tested end to end."""

    def __init__(self, logs=None):
        self.functions, self.calls, self.logs = {}, [], logs
        self.polls_until_active = 1  # get_function calls before a new/updated function is Active
        self.fail_create = None  # message: new functions end up Failed
        self.invoke_error = None  # (code, message): invoke itself fails (e.g. TooManyRequestsException)
        self.function_error = None  # message: the function "crashes" with this errorMessage
        self.concurrency_error = False

    @staticmethod
    def _error(code, message="", op="Call"):
        return ClientError({"Error": {"Code": code, "Message": message}}, op)

    def _get(self, name):
        if name not in self.functions:
            raise self._error("ResourceNotFoundException", f"Function not found: {name}")
        return self.functions[name]

    def create_function(self, **kw):
        self.calls.append(("create_function", kw["FunctionName"]))
        name = kw["FunctionName"]
        if name in self.functions:
            raise self._error("ResourceConflictException", "exists", "CreateFunction")
        assert name.startswith("sd-") and kw["Role"] and kw["Runtime"] in ("nodejs22.x", "python3.12")
        self.functions[name] = {
            "zip": store[kw["Code"]["S3Key"]]["Body"], "polls": 0, "updating": False, "concurrency": None, "tags": kw.get("Tags", {}),
            "config": {"FunctionName": name, "Runtime": kw["Runtime"], "Handler": kw["Handler"], "Role": kw["Role"],
                       "MemorySize": kw["MemorySize"], "Timeout": kw["Timeout"], "State": "Pending",
                       "Environment": kw.get("Environment") or {"Variables": {}}},
        }

    def update_function_code(self, FunctionName, S3Bucket, S3Key):
        self.calls.append(("update_function_code", FunctionName))
        function = self._get(FunctionName)
        function.update(zip=store[S3Key]["Body"], polls=0, updating=True)

    def get_function(self, FunctionName):
        function = self._get(FunctionName)
        function["polls"] += 1
        config = dict(function["config"])
        if self.fail_create:
            config.update(State="Failed", StateReason=self.fail_create)
        elif function["polls"] >= self.polls_until_active:
            function["config"]["State"], function["updating"] = "Active", False
            config.update(State="Active", LastUpdateStatus="Successful")
        else:
            config.update(State="Pending" if config["State"] == "Pending" else "Active", LastUpdateStatus="InProgress")
        return {"Configuration": config}

    def get_function_configuration(self, FunctionName):
        return dict(self._get(FunctionName)["config"])

    def update_function_configuration(self, FunctionName, Environment=None, Timeout=None):
        function = self._get(FunctionName)
        if function["updating"]:
            raise self._error("ResourceConflictException", "update in progress")
        if Environment is not None:
            function["config"]["Environment"] = Environment
        if Timeout is not None:
            function["config"]["Timeout"] = Timeout
        self.calls.append(("update_function_configuration", FunctionName))

    def delete_function(self, FunctionName):
        self._get(FunctionName)
        self.calls.append(("delete_function", FunctionName))
        del self.functions[FunctionName]

    def put_function_concurrency(self, FunctionName, ReservedConcurrentExecutions):
        if self.concurrency_error:
            raise self._error("InvalidParameterValueException", "unreserved concurrency below minimum")
        self._get(FunctionName)["concurrency"] = ReservedConcurrentExecutions
        self.calls.append(("put_function_concurrency", FunctionName, ReservedConcurrentExecutions))

    def delete_function_concurrency(self, FunctionName):
        self._get(FunctionName)["concurrency"] = None
        self.calls.append(("delete_function_concurrency", FunctionName))

    # ---- invoke: runs the student's real code ----
    def invoke(self, FunctionName, InvocationType, Payload):
        self.calls.append(("invoke", FunctionName))
        if self.invoke_error:
            raise self._error(*self.invoke_error, "Invoke")
        function = self._get(FunctionName)
        if function["concurrency"] == 0:
            raise self._error("TooManyRequestsException", "Rate Exceeded.", "Invoke")
        event = json.loads(Payload)
        if self.function_error:
            return {"StatusCode": 200, "FunctionError": "Unhandled", "Payload": Body(json.dumps({"errorMessage": self.function_error}).encode())}
        runner = self._run_node if function["config"]["Runtime"].startswith("nodejs") else self._run_python
        try:
            result, output = runner(function, event)
        except Exception as e:  # the handler crashed
            return {"StatusCode": 200, "FunctionError": "Unhandled",
                    "Payload": Body(json.dumps({"errorMessage": str(e)[:300], "errorType": type(e).__name__}).encode())}
        if self.logs is not None:
            for line in output.splitlines():
                self.logs.add(f"/aws/lambda/{FunctionName}", line)
        return {"StatusCode": 200, "Payload": Body(json.dumps(result).encode())}

    @staticmethod
    def _env(function):
        env = dict((function["config"].get("Environment") or {}).get("Variables") or {})
        env["AWS_LAMBDA_FUNCTION_NAME"] = function["config"]["FunctionName"]  # real Lambda sets this for every function
        return env

    def _run_python(self, function, event):
        import contextlib
        import importlib.util
        import sys
        import tempfile
        import zipfile

        class Context:
            function_name = function["config"]["FunctionName"]

            @staticmethod
            def get_remaining_time_in_millis():
                return function["config"]["Timeout"] * 1000

        with tempfile.TemporaryDirectory() as folder:
            zipfile.ZipFile(io.BytesIO(function["zip"])).extractall(folder)
            saved_env, saved_path = dict(os.environ), list(sys.path)
            out = io.StringIO()
            try:
                os.environ.update(self._env(function))
                sys.path.insert(0, folder)
                spec = importlib.util.spec_from_file_location("student_app", os.path.join(folder, "app.py"))
                module = importlib.util.module_from_spec(spec)
                with contextlib.redirect_stdout(out):
                    spec.loader.exec_module(module)
                    result = module.handler(event, Context())
            finally:
                os.environ.clear()
                os.environ.update(saved_env)
                sys.path[:] = saved_path
        return result, out.getvalue()

    def _run_node(self, function, event):
        import subprocess
        import tempfile
        import zipfile
        entry = "index.mjs" if "index.mjs" in zipfile.ZipFile(io.BytesIO(function["zip"])).namelist() else "index.js"
        runner = ("import { pathToFileURL } from 'node:url';\n"
                  f"const m = await import(pathToFileURL(process.cwd() + '/{entry}').href);\n"
                  "const handler = m.handler ?? m.default?.handler;\n"
                  "const result = await handler(JSON.parse(process.env.EVENT), {});\n"
                  "console.log('__RESULT__' + JSON.stringify(result ?? null));\n")
        with tempfile.TemporaryDirectory() as folder:
            zipfile.ZipFile(io.BytesIO(function["zip"])).extractall(folder)
            with open(os.path.join(folder, "__runner.mjs"), "w", encoding="utf8") as f:
                f.write(runner)
            done = subprocess.run(["node", "__runner.mjs"], cwd=folder, capture_output=True, text=True, timeout=20,
                                  env={**os.environ, **self._env(function), "EVENT": json.dumps(event)})
        if done.returncode != 0:
            raise RuntimeError((done.stderr.strip().splitlines() or ["node failed"])[-1])
        lines = done.stdout.splitlines()
        result = next(json.loads(l[len("__RESULT__"):]) for l in reversed(lines) if l.startswith("__RESULT__"))
        return result, "\n".join(l for l in lines if not l.startswith("__RESULT__"))


class FakeOpenRouter:
    """Stands in for openrouter.ai: the key-management API with metering. Plug it into openrouter.transport.
    Mirrors what the live spike showed: only a management key may manage keys; expires_at cannot be patched; a patch with no
    known field is a 400; usage is added with spend()."""

    def __init__(self, management_key="mgmt-secret"):
        self.management_key, self.keys, self.calls, self.fail, self._n = management_key, {}, [], None, 0

    @staticmethod
    def data(k):
        return {"hash": k["hash"], "name": k["name"], "limit": k["limit"], "limit_reset": k["limit_reset"], "disabled": k["disabled"],
                "usage": k["usage"], "limit_remaining": None if k["limit"] is None else max(k["limit"] - k["usage"], 0)}

    def spend(self, key_hash, usd):
        self.keys[key_hash]["usage"] += usd

    def by_name(self, name):
        return next((k for k in self.keys.values() if k["name"] == name), None)

    def __call__(self, method, path, token, body):
        self.calls.append((method, path, body))
        if self.fail:
            return self.fail[0], {"error": {"message": self.fail[1]}}
        if token != self.management_key:
            return 403, {"error": {"message": "Only management keys can perform this operation"}}
        if (method, path) == ("POST", "/keys"):
            assert isinstance(body["limit"], (int, float)) and body["limit_reset"] in (None, "daily", "weekly", "monthly"), body
            self._n += 1
            k = {"hash": f"h{self._n:03d}", "key": f"sk-or-v1-fake-{self._n:03d}", "name": body["name"], "limit": body["limit"],
                 "limit_reset": body["limit_reset"], "disabled": False, "usage": 0.0}
            self.keys[k["hash"]] = k
            return 201, {"data": self.data(k), "key": k["key"]}
        found = re.fullmatch(r"/keys/(\w+)", path)
        k = self.keys.get(found.group(1)) if found else None
        if not k:
            return 404, {"error": {"message": "Key not found"}}
        if method == "GET":
            return 200, {"data": self.data(k)}
        if method == "DELETE":
            del self.keys[k["hash"]]
            return 200, {"deleted": True}
        if method == "PATCH":
            known = {f: v for f, v in body.items() if f in ("limit", "limit_reset", "disabled")}
            if not known:
                return 400, {"error": {"message": "No update fields provided"}}
            k.update(known)
            return 200, {"data": self.data(k)}
        return 405, {"error": {"message": "bad method"}}
