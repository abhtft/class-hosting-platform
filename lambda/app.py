"""Student frontend deploy API.

POST /upload-url  -> presigned S3 PUT URL for uploads/<student>.zip
POST /publish     -> unzip into sites/<student>/, remove stale files, invalidate CloudFront

Auth: header `x-deploy-key: <student>.<secret>`, checked against the
{"student": "secret"} JSON map stored in SSM Parameter Store.
"""
import hmac
import io
import json
import mimetypes
import os
import posixpath
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

BUCKET = os.environ["BUCKET"]
DISTRIBUTION_ID = os.environ["DISTRIBUTION_ID"]
DISTRIBUTION_DOMAIN = os.environ["DISTRIBUTION_DOMAIN"]
ROOT_DOMAIN = os.environ.get("ROOT_DOMAIN", "")
KEYS_PARAM = os.environ["KEYS_PARAM"]

MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_UNZIPPED_BYTES = 200 * 1024 * 1024
MAX_FILES = 2000
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$")
JUNK = ("__MACOSX/", ".DS_Store", "Thumbs.db")

EXTRA_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".json": "application/json",
    ".map": "application/json",
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".wasm": "application/wasm",
    ".txt": "text/plain",
}

s3 = boto3.client(
    "s3",
    region_name=os.environ.get("AWS_REGION"),
    config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}, max_pool_connections=32),
)
ssm = boto3.client("ssm")
cloudfront = boto3.client("cloudfront")

_keys = {"loaded_at": 0.0, "value": {}}


class DeployError(Exception):
    pass


def handler(event, context):
    path = event.get("rawPath", "")
    if path.startswith("/api/"):
        import portal
        portal.legacy_names = lambda: set(load_keys())
        portal.restore_site, portal.archive_site = restore_site, archive_site
        if path in ("/api/student/upload-url", "/api/student/publish") and event["requestContext"]["http"]["method"] == "POST":
            return portal_deploy(portal, event, publish_now=path.endswith("/publish"))
        return portal.handle(event, site_url)

    name = authenticate(event)
    if not name:
        return reply(401, error="Invalid or missing x-deploy-key. Ask your teacher for your deploy key.")

    route = event.get("routeKey", "")
    try:
        if route == "POST /upload-url":
            return upload_url(name)
        if route == "POST /publish":
            return publish(name)
    except DeployError as e:
        return reply(400, error=str(e))
    return reply(404, error=f"Unknown route {route}")


def portal_deploy(portal, event, publish_now):
    """Deploy for a web-portal student: the session decides the site folder, so a student can only touch their own."""
    now = int(time.time())
    try:
        item = portal.student_token(event, now)
        if portal.status_of(item, now) != "active":
            raise portal.PortalError(403, "Your token has expired, so you can't deploy. Request regeneration from your dashboard.")
        if not publish_now:
            return upload_url(item["slug"])
        result = publish(item["slug"], tenant_id=item.get("tenant_id"))
        if result["statusCode"] == 200:
            portal.db().update(item["pk"], {"last_deploy_at": now})
        return result
    except portal.PortalError as e:
        return reply(e.status, error=str(e))
    except DeployError as e:
        return reply(400, error=str(e))


def authenticate(event):
    key = (event.get("headers") or {}).get("x-deploy-key", "")
    name, _, _ = key.partition(".")
    if not NAME_RE.match(name) or name in ("sites", "uploads"):
        return None
    secret = load_keys().get(name)
    if not secret or not hmac.compare_digest(key.encode(), f"{name}.{secret}".encode()):
        return None
    return name


def load_keys():
    # Cache briefly so adding/removing a student takes effect within a minute.
    if time.time() - _keys["loaded_at"] > 60:
        value = ssm.get_parameter(Name=KEYS_PARAM)["Parameter"]["Value"]
        _keys["value"] = json.loads(value or "{}")
        _keys["loaded_at"] = time.time()
    return _keys["value"]


def upload_url(name):
    url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": BUCKET, "Key": f"uploads/{name}.zip"},
        ExpiresIn=600,
    )
    return reply(200, uploadUrl=url, maxBytes=MAX_ZIP_BYTES)


def publish(name, tenant_id=None):
    zip_key = f"uploads/{name}.zip"
    try:
        size = s3.head_object(Bucket=BUCKET, Key=zip_key)["ContentLength"]
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            raise DeployError("No upload found. Call /upload-url and PUT your zip first.")
        raise
    if size > MAX_ZIP_BYTES:
        s3.delete_object(Bucket=BUCKET, Key=zip_key)
        raise DeployError(f"Zip is {size // 1024 // 1024} MB; the limit is {MAX_ZIP_BYTES // 1024 // 1024} MB.")

    body = s3.get_object(Bucket=BUCKET, Key=zip_key)["Body"].read()
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile:
        raise DeployError("Upload is not a valid zip file.")

    entries = site_entries(archive)
    prefix = f"sites/{name}/"

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(put_file, prefix + path, archive.read(info), path) for path, info in entries.items()]
        for future in futures:
            future.result()

    stale = [key for key in list_keys(prefix) if key[len(prefix):] not in entries]
    delete_keys(stale)

    invalidate(name, tenant_id)
    s3.delete_object(Bucket=BUCKET, Key=zip_key)

    result = {"url": site_url(name), "files": len(entries), "removed": len(stale)}
    warning = base_path_warning(name, archive.read(entries["index.html"]))
    if warning:
        result["warning"] = warning
    return reply(200, **result)


def invalidate(name, tenant_id=None):
    # Cache entries are keyed by the rewritten URI (/sites/<name>/...); /<name>/* covers the viewer path in path mode.
    paths = [f"/sites/{name}/*", f"/{name}/*"]
    cloudfront.create_invalidation(
        DistributionId=DISTRIBUTION_ID,
        InvalidationBatch={"Paths": {"Quantity": len(paths), "Items": paths}, "CallerReference": f"{name}-{time.time_ns()}"},
    )
    if tenant_id:
        # The student's custom domain is a separate CloudFront tenant with its own cache (objects live up to a year).
        # A failure here must not fail the deploy: the site is already published on the main address.
        try:
            cloudfront.create_invalidation_for_distribution_tenant(
                Id=tenant_id,
                InvalidationBatch={"Paths": {"Quantity": 1, "Items": ["/*"]}, "CallerReference": f"{name}-{time.time_ns()}"},
            )
        except Exception as e:
            print(f"custom-domain cache invalidation failed for {name}: {e}")


def delete_keys(keys):
    for i in range(0, len(keys), 1000):
        s3.delete_objects(Bucket=BUCKET, Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]], "Quiet": True})


def restore_site(name, tenant_id=None):
    """Move a site taken offline (archive/<name>/) back to sites/<name>/."""
    src, dst = f"archive/{name}/", f"sites/{name}/"
    keys = list_keys(src)
    for key in keys:
        s3.copy_object(Bucket=BUCKET, Key=dst + key[len(src):], CopySource={"Bucket": BUCKET, "Key": key})
    delete_keys(keys)
    invalidate(name, tenant_id)


def archive_site(name, tenant_id=None):
    """Take a site offline by moving sites/<name>/ to archive/<name>/ (CloudFront only serves sites/*)."""
    src, dst = f"sites/{name}/", f"archive/{name}/"
    keys = list_keys(src)
    for key in keys:
        s3.copy_object(Bucket=BUCKET, Key=dst + key[len(src):], CopySource={"Bucket": BUCKET, "Key": key})
    delete_keys([k for k in list_keys(dst) if src + k[len(dst):] not in keys])  # leftovers of an older archive
    delete_keys(keys)
    invalidate(name, tenant_id)


def sweep(event, context):
    import portal
    portal.archive_site = archive_site
    result = portal.sweep()
    print(json.dumps(result))
    if result["failed"]:
        raise RuntimeError(f"{len(result['failed'])} site(s) could not be archived: {result['failed']}")
    return result


def site_entries(archive):
    """Return {relative/path: ZipInfo} for the site, validated and rooted at index.html."""
    entries = {}
    total = 0
    for info in archive.infolist():
        if info.is_dir() or any(j in info.filename for j in JUNK):
            continue
        raw = info.filename.replace("\\", "/")
        path = posixpath.normpath(raw)
        if raw.startswith("/") or path == "." or path.startswith("../") or path == "..":
            raise DeployError(f"Unsafe path in zip: {info.filename}")
        entries[path] = info
        total += info.file_size

    if len(entries) > MAX_FILES:
        raise DeployError(f"Too many files ({len(entries)}); the limit is {MAX_FILES}.")
    if total > MAX_UNZIPPED_BYTES:
        raise DeployError(f"Site is {total // 1024 // 1024} MB unzipped; the limit is {MAX_UNZIPPED_BYTES // 1024 // 1024} MB.")

    # Accept a zip of the build folder itself (dist/index.html) as well as of its contents.
    if "index.html" not in entries and entries and all("/" in p for p in entries):
        tops = {p.split("/", 1)[0] for p in entries}
        if len(tops) == 1:
            cut = len(tops.pop()) + 1
            entries = {p[cut:]: info for p, info in entries.items()}

    if "index.html" not in entries:
        raise DeployError("index.html not found at the top of the zip. Zip the contents of your build folder (e.g. dist/).")
    return entries


def put_file(key, data, path):
    s3.put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=content_type(path), CacheControl=cache_control(path))


def content_type(path):
    ext = posixpath.splitext(path)[1].lower()
    guessed = EXTRA_TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
    if guessed.startswith("text/") or guessed in ("application/json", "application/manifest+json"):
        guessed += "; charset=utf-8"
    return guessed


def cache_control(path):
    # CloudFront keeps everything until the next publish invalidates it (s-maxage);
    # browsers always revalidate HTML and keep other files for 5 minutes.
    if path.endswith(".html"):
        return "public, max-age=0, must-revalidate, s-maxage=31536000"
    return "public, max-age=300, s-maxage=31536000"


def list_keys(prefix):
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def site_url(name):
    if ROOT_DOMAIN:
        return f"https://{name}.{ROOT_DOMAIN}/"
    return f"https://{DISTRIBUTION_DOMAIN}/{name}/"


def base_path_warning(name, index_html):
    """In path mode, root-absolute asset URLs (/assets/x.js) miss the /<name>/ prefix and 404."""
    if ROOT_DOMAIN:
        return None
    if re.search(rb'(?:src|href)="/(?!/)', index_html) and f'"/{name}/'.encode() not in index_html:
        return f"Your index.html loads files from '/...'. Set base: '/{name}/' in vite.config to make them load from your folder."
    return None


def reply(status, **body):
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}
