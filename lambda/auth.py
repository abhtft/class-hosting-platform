"""Sign-in for the apps students build (docs/AUTH_PLAN.md, option A, step A1).

One Amazon Cognito user pool serves the whole platform. It has one sign-in domain and one Google registration, which the teacher sets up once.
Each student app gets its own app client with its own return address, so one app's client can be removed without touching the others.
Apps use a public client with PKCE, so no client secret is stored in an app or in the database.

Records:
  platform: one item "authpool#platform": pool_id, domain, the flags of each one-time step (idempotent, so a failed run can resume).
  student:  item["auth"] on the student's token item: enabled, client_id, callback, logout, pool_id. It never holds a secret.

The Google client id and secret are read from AWS Systems Manager (a SecureString named in AUTH_GOOGLE_PARAM), as the AI keys are. Nothing is written
into code or into the database.
"""
import hashlib
import json
import os

from botocore.exceptions import ClientError

import backends
from portal import PortalError, db

PLATFORM_KEY = "authpool#platform"
POOL_NAME = "class-platform-visitors"
PROVIDER = "Google"
SCOPES = ["openid", "email", "profile"]
CALLBACK_PATH = "/api/auth/callback"


def google_param():
    return os.environ.get("AUTH_GOOGLE_PARAM", "/student-deploy/auth-google")


def client(name):
    return backends.client(name)


def google_credentials():
    """The Google client id and secret the teacher stored. Missing or malformed means sign-in is not set up yet."""
    try:
        raw = client("ssm").get_parameter(Name=google_param(), WithDecryption=True)["Parameter"]["Value"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            raise PortalError(503, "Google sign-in is not set up yet. The teacher stores the Google client once (see docs/AUTH_PLAN.md).")
        raise PortalError(502, "Sign-in is temporarily unavailable. Try again in a moment.")
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict) or not data.get("client_id") or not data.get("client_secret"):
        raise PortalError(500, "The stored Google sign-in setting is not valid. The teacher must store it again (see docs/AUTH_PLAN.md).")
    return str(data["client_id"]), str(data["client_secret"])


def app_base(slug):
    """The address the student's app is served from (the same rule as the platform's site addresses)."""
    root = os.environ.get("ROOT_DOMAIN", "")
    if root:
        return f"https://{slug}.{root}"
    return f"https://{os.environ['DISTRIBUTION_DOMAIN']}/{slug}"


def _domain_name():
    digest = hashlib.sha256(os.environ.get("ROOT_DOMAIN", os.environ.get("DISTRIBUTION_ID", "")).encode()).hexdigest()[:8]
    return f"sd-class-{digest}"


def _save_platform(fields):
    if not db().put({"pk": PLATFORM_KEY, **fields}, if_absent=True):
        db().update(PLATFORM_KEY, fields)


def platform_pool():
    """The shared pool, created once. Each step is recorded as it finishes, so a failure part way can be finished later."""
    record = db().get(PLATFORM_KEY) or {}
    client_id, secret = google_credentials()  # checked first: nothing is created unless Google is set up
    cg = client("cognito-idp")
    if not record.get("pool_id"):
        pool_id = cg.create_user_pool(
            PoolName=POOL_NAME, UsernameAttributes=["email"], AutoVerifiedAttributes=["email"], MfaConfiguration="OFF",
            AdminCreateUserConfig={"AllowAdminCreateUserOnly": False}, UserPoolTags={"platform": "class-platform"},
        )["UserPool"]["Id"]
        _save_platform({"pool_id": pool_id, "domain": _domain_name(), "domain_made": False, "google_made": False})
        record = db().get(PLATFORM_KEY)
    if not record.get("domain_made"):
        cg.create_user_pool_domain(Domain=record["domain"], UserPoolId=record["pool_id"])
        _save_platform({"domain_made": True})
    if not record.get("google_made"):
        cg.create_identity_provider(
            UserPoolId=record["pool_id"], ProviderName=PROVIDER, ProviderType=PROVIDER,
            ProviderDetails={"client_id": client_id, "client_secret": secret, "authorize_scopes": " ".join(SCOPES)},
            AttributeMapping={"email": "email", "name": "name", "username": "sub"},
        )
        _save_platform({"google_made": True})
    return db().get(PLATFORM_KEY)


def enable_app(item):
    """Give one student app its own client in the shared pool, with its own return address. Asking again changes nothing."""
    current = dict(item.get("auth") or {})
    if current.get("enabled") and current.get("client_id"):
        return current
    slug = item["slug"]
    pool = platform_pool()
    base = app_base(slug)
    callback = base + CALLBACK_PATH
    client_id = client("cognito-idp").create_user_pool_client(
        UserPoolId=pool["pool_id"], ClientName=f"app-{slug}", GenerateSecret=False,
        AllowedOAuthFlows=["code"], AllowedOAuthFlowsUserPoolClient=True, AllowedOAuthScopes=SCOPES,
        CallbackURLs=[callback], LogoutURLs=[base + "/"], SupportedIdentityProviders=[PROVIDER],
        ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH"], PreventUserExistenceErrors="ENABLED", EnableTokenRevocation=True,
    )["UserPoolClient"]["ClientId"]
    auth = {"enabled": True, "client_id": client_id, "callback": callback, "logout": base + "/", "pool_id": pool["pool_id"]}
    db().update(item["pk"], {"auth": auth})
    return auth


def disable_app(item):
    """Remove the app's client, so no new sign-ins reach the app. The pool and the other apps are not touched."""
    current = dict(item.get("auth") or {})
    if current.get("client_id") and current.get("pool_id"):
        try:
            client("cognito-idp").delete_user_pool_client(UserPoolId=current["pool_id"], ClientId=current["client_id"])
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise PortalError(502, "Could not turn sign-in off just now. Try again in a moment.")
    auth = {"enabled": False}
    db().update(item["pk"], {"auth": auth})
    return auth
