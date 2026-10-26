"""Guards the documented configuration of the multi-tenant distribution used for student custom domains.

A custom domain is served by a different distribution than <slug>.example.edu, so it needs its own /api/* route to the
backend. This file once lacked it, and every POST to a custom domain got a CloudFront 403. These checks keep the documented
target configuration complete and free of real secrets."""
import json
import os

root = os.path.join(os.path.dirname(__file__), "..")
config = json.load(open(os.path.join(root, "custom-domain", "tenant-distribution.json"), encoding="utf8"))
text = open(os.path.join(root, "custom-domain", "tenant-distribution.json"), encoding="utf8").read()

origins = {o["Id"]: o for o in config["Origins"]["Items"]}
assert set(origins) == {"sites", "backend"}, origins.keys()
assert origins["sites"]["OriginPath"] == "/sites/{{student}}"
backend = origins["backend"]
assert backend["OriginPath"] == "/b/{{student}}", "the tenant parameter selects the student's backend"
assert backend["CustomOriginConfig"]["OriginProtocolPolicy"] == "https-only"
header = backend["CustomHeaders"]["Items"][0]
assert header["HeaderName"] == "x-origin-secret" and header["HeaderValue"] == "<ORIGIN_SECRET>", "never commit the real secret"
assert backend["DomainName"] == "<BACKEND_API_HOST>"
assert config["Origins"]["Quantity"] == 2 and config["CacheBehaviors"]["Quantity"] == 1

behavior = config["CacheBehaviors"]["Items"][0]
assert behavior["PathPattern"] == "/api/*" and behavior["TargetOriginId"] == "backend"
assert {"GET", "POST", "PUT", "DELETE", "OPTIONS"} <= set(behavior["AllowedMethods"]["Items"]), "POST must be allowed"
assert behavior["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad", "API responses are never cached"
assert behavior["OriginRequestPolicyId"] == "b689b0a8-53d0-40ab-baf2-68738e2966ac", "Authorization must reach the function"
assert behavior["ViewerProtocolPolicy"] == "https-only"
assert behavior["FunctionAssociations"]["Items"][0]["FunctionARN"].endswith(":function/student-hosting-tenant-api")

# the default behavior still serves the site (GET/HEAD + SPA fallback) and must not swallow /api
default = config["DefaultCacheBehavior"]
assert default["TargetOriginId"] == "sites" and set(default["AllowedMethods"]["Items"]) == {"GET", "HEAD"}
assert default["FunctionAssociations"]["Items"][0]["FunctionARN"].endswith(":function/student-hosting-spa-fallback")

# nothing in the file looks like a real secret (the origin secret is the stack id's UUID). AWS's own managed policy IDs are
# public UUID-shaped values, so those known ones are removed before looking.
import re  # noqa: E402
for public_id in ("658327ea-f89d-4fab-a63d-7e88639e58f6", "4135ea2d-6df8-44a3-9df3-4b5a84be39ad", "b689b0a8-53d0-40ab-baf2-68738e2966ac"):
    text = text.replace(public_id, "")
assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text), "a UUID-shaped value (the origin secret?) is in the file"
print("test_tenant_config.py: all passed")
