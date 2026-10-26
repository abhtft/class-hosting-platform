"""Give the multi-tenant distribution (student CUSTOM DOMAINS) its /api/* route to the Backend API.

Why: students' backends are reached at <site>/api/... The normal address (<slug>.example.edu) gets that route from
template.yaml. Custom domains are served by a separate, hand-made distribution that is NOT in the template, so without this
step a POST to https://<custom-domain>/api/... is rejected by CloudFront with an HTML 403 page.

    python custom-domain/apply-tenant-api-route.py            DRY RUN: shows the planned change, changes nothing
    python custom-domain/apply-tenant-api-route.py --apply    performs it

Before it, publish the CloudFront function once (source: custom-domain/tenant-api-route.js):
    aws cloudfront create-function --name student-hosting-tenant-api --function-config "Comment=Tenant /api route,Runtime=cloudfront-js-2.0" --function-code fileb://custom-domain/tenant-api-route.js
    aws cloudfront publish-function --name student-hosting-tenant-api --if-match <ETag from the previous command>

Safe to repeat (does nothing if already configured). The CURRENT configuration is saved to a temp file first; rolling back
means update-distribution with that file. The shared secret is read from the stack at run time and never printed or stored
in the repo (custom-domain/tenant-distribution.json holds a placeholder).
"""
import json
import os
import sys
import tempfile

import boto3

DISTRIBUTION_ID = os.environ.get("TENANT_DISTRIBUTION_ID", "<TENANT_DISTRIBUTION_ID>")
STACK = os.environ.get("STACK_NAME", "student-hosting")
FUNCTION_NAME = "student-hosting-tenant-api"

cloudfront = boto3.client("cloudfront", region_name="us-east-1")
stack = boto3.client("cloudformation", region_name="us-east-1").describe_stacks(StackName=STACK)["Stacks"][0]
outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
if "BackendApiUrl" not in outputs:
    sys.exit("The backend feature is not deployed (no BackendApiUrl output): nothing to route to.")
api_host = outputs["BackendApiUrl"].split("//")[1]
secret = stack["StackId"].split("/")[2]  # the same value template.yaml puts in the student distribution
function_arn = cloudfront.get_function(Name=FUNCTION_NAME, Stage="LIVE")  # raises if the function is not published
function_arn = f"arn:aws:cloudfront::{stack['StackId'].split(':')[4]}:function/{FUNCTION_NAME}"

current = cloudfront.get_distribution_config(Id=DISTRIBUTION_ID)
config, etag = current["DistributionConfig"], current["ETag"]
backup = os.path.join(tempfile.gettempdir(), f"{DISTRIBUTION_ID}-before-api-route.json")
json.dump(config, open(backup, "w"), indent=2, default=str)
print(f"current configuration saved to {backup}")
if any(o["Id"] == "backend" for o in config["Origins"]["Items"]):
    sys.exit("Already configured: nothing to do.")

config["Origins"]["Items"].append({
    "Id": "backend", "DomainName": api_host, "OriginPath": "/b/{{student}}",
    "CustomHeaders": {"Quantity": 1, "Items": [{"HeaderName": "x-origin-secret", "HeaderValue": secret}]},
    "CustomOriginConfig": {"HTTPPort": 80, "HTTPSPort": 443, "OriginProtocolPolicy": "https-only",
                           "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]}, "OriginReadTimeout": 30, "OriginKeepaliveTimeout": 5},
    "ConnectionAttempts": 3, "ConnectionTimeout": 10,
})
config["Origins"]["Quantity"] = len(config["Origins"]["Items"])
config["CacheBehaviors"] = {"Quantity": 1, "Items": [{
    "PathPattern": "/api/*", "TargetOriginId": "backend", "ViewerProtocolPolicy": "https-only",
    "AllowedMethods": {"Quantity": 7, "Items": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"],
                       "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
    "Compress": False, "FieldLevelEncryptionId": "", "LambdaFunctionAssociations": {"Quantity": 0},
    "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",         # Managed-CachingDisabled
    "OriginRequestPolicyId": "b689b0a8-53d0-40ab-baf2-68738e2966ac",  # Managed-AllViewerExceptHostHeader (forwards Authorization)
    "FunctionAssociations": {"Quantity": 1, "Items": [{"EventType": "viewer-request", "FunctionARN": function_arn}]},
}]}

print("\nPLANNED CHANGE to distribution", DISTRIBUTION_ID, "(secret value hidden):")
print("  + origin   backend ->", api_host, "| origin path /b/{{student}} | header x-origin-secret | https-only, TLS 1.2")
print("  + behavior /api/*  -> backend | all methods | caching disabled | forwards Authorization | function", FUNCTION_NAME)
print("  unchanged: the S3 origin, the default behavior (GET/HEAD + SPA fallback), every tenant and certificate")
if "--apply" not in sys.argv:
    sys.exit("\nDRY RUN: nothing was changed. Re-run with --apply to perform it.")
result = cloudfront.update_distribution(Id=DISTRIBUTION_ID, IfMatch=etag, DistributionConfig=config)
print("Update accepted. Status:", result["Distribution"]["Status"], "(wait for Deployed, then test a POST on a custom domain)")
