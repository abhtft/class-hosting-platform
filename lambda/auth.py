"""Sign-in for the apps students build (docs/AUTH_PLAN.md, option A, step A1).

One Amazon Cognito user pool serves the whole platform. It has one sign-in domain and one Google registration, which the teacher sets up once.
Each student app gets its own app client with its own return address, so one app's client can be removed without touching the others.
Apps use a public client with PKCE, so no client secret is stored in an app or in the database.

Records:
  platform: one item "authpool#platform": pool_id, domain, the flags of each one-time step (idempotent, so a failed run can resume).
  student:  item["auth"] on the student's token item: enabled, client_id, callback, logout, pool_id, and the app's own session key
            (a random key that signs the app's session cookie; kept like the class AI key). It never holds a Google secret.

The Google client id and secret are read from AWS Systems Manager (a SecureString named in AUTH_GOOGLE_PARAM), as the AI keys are. Nothing is written
into code or into the database.
"""
import hashlib
import json
import os
import secrets

from botocore.exceptions import ClientError

import backends
from portal import PortalError, db
import platform_settings

PLATFORM_KEY = "authpool#platform"
POOL_NAME = "class-platform-visitors"
PROVIDER = "Google"
SCOPES = ["openid", "email", "profile"]
CALLBACK_PATH = "/api/auth/callback"
ENV_NAMES = ("CLASS_AUTH_HOST", "CLASS_AUTH_CLIENT_ID", "CLASS_AUTH_ISSUER", "CLASS_AUTH_REDIRECT", "CLASS_AUTH_HOME", "CLASS_AUTH_KEY", "CLASS_AUTH_DAYS")


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
            AdminCreateUserConfig={"AllowAdminCreateUserOnly": False},  # no tags: tagging needs one more permission, and the pool is named for the platform
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
    try:
        pool = platform_pool()
        base = app_base(slug)
        callback = base + CALLBACK_PATH
        client_id = client("cognito-idp").create_user_pool_client(
            UserPoolId=pool["pool_id"], ClientName=f"app-{slug}", GenerateSecret=False,
            AllowedOAuthFlows=["code"], AllowedOAuthFlowsUserPoolClient=True, AllowedOAuthScopes=SCOPES,
            CallbackURLs=[callback], LogoutURLs=[base + "/"], SupportedIdentityProviders=[PROVIDER],
            ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH"], PreventUserExistenceErrors="ENABLED", EnableTokenRevocation=True,
        )["UserPoolClient"]["ClientId"]
    except ClientError as e:  # a plain message with the AWS error code (never the details), so the teacher can find the cause in the platform's log
        raise PortalError(502, f"Could not set up sign-in ({e.response['Error'].get('Code', 'AWS error')}). Tell your teacher; the platform's log has the details. Nothing was turned on.")
    auth = {"enabled": True, "client_id": client_id, "callback": callback, "logout": base + "/", "pool_id": pool["pool_id"],
            "domain": pool["domain"], "session_key": secrets.token_urlsafe(32)}
    apply_to_function(db().update(item["pk"], {"auth": auth}))
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
    apply_to_function(db().update(item["pk"], {"auth": auth}))
    return auth


def is_on(item):
    return bool((item.get("auth") or {}).get("enabled"))


def reserved_name(item, name):
    """These names always belong to the platform: a student never sets the sign-in values by hand."""
    return name in ENV_NAMES


def env_for(item):
    """What the app's function needs to sign visitors in. Empty while sign-in is off."""
    a = item.get("auth") or {}
    if not a.get("enabled"):
        return {}
    region = os.environ.get("AWS_REGION", "us-east-1")
    return {
        "CLASS_AUTH_HOST": f"https://{a['domain']}.auth.{region}.amazoncognito.com",
        "CLASS_AUTH_CLIENT_ID": a["client_id"],
        "CLASS_AUTH_ISSUER": f"https://cognito-idp.{region}.amazonaws.com/{a['pool_id']}",
        "CLASS_AUTH_REDIRECT": a["callback"],
        "CLASS_AUTH_HOME": a["logout"],
        "CLASS_AUTH_KEY": a["session_key"],
        "CLASS_AUTH_DAYS": str(platform_settings.value("auth.sessionDays")),
    }


def apply_to_function(item):
    """Make the app's function match the sign-in state (its environment values). A no-op until a function exists."""
    name = backends.settings(item).get("function_name")
    if not name:
        return item
    try:
        variables = backends._current_env(name)
        for var in ENV_NAMES:
            variables.pop(var, None)
        variables.update(env_for(item))
        backends.client("lambda").update_function_configuration(FunctionName=name, Environment={"Variables": variables})
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            raise PortalError(409, "Your function is updating. Try again in a few seconds; nothing is lost.")
        raise PortalError(502, "Could not update your function with the sign-in settings. Try again in a moment.")
    import jobs
    jobs.mirror_env(item, variables)
    return backends._save(item, env_names=sorted(variables))


def login_host(domain):
    """The sign-in address of the shared pool (Cognito's hosted pages on the pool's own domain)."""
    return f"https://{domain}.auth.{os.environ.get('AWS_REGION', 'us-east-1')}.amazoncognito.com"


def google_return_address(domain):
    """The address to register on the Google client: Cognito's answer page for the pool. Registered once, shared by every app.
    The domain is fixed in advance (see _domain_name), so the teacher can register it BEFORE the pool exists."""
    return login_host(domain) + "/oauth2/idpresponse"


def _addresses():
    domain = (db().get(PLATFORM_KEY) or {}).get("domain") or _domain_name()
    return {"google_return_address": google_return_address(domain), "login_host": login_host(domain)}


def student_route(item, method, body):
    """GET /api/student/sign-in: the state and the addresses. POST {enabled: true|false}: turn sign-in on or off for this app.
    Sign-in runs in the student's backend, so the backend must be on first."""
    if not backends.settings(item).get("enabled"):
        raise PortalError(403, "Sign-in needs your backend. Ask your teacher to turn the backend on for you first.")
    current = dict(item.get("auth") or {})
    if method == "GET":
        return 200, {"enabled": bool(current.get("enabled")), "callback": current.get("callback"), "logout": current.get("logout"), **_addresses()}
    if method != "POST":
        raise PortalError(405, "Use GET to see sign-in, or POST with enabled true or false.")
    flag = (body or {}).get("enabled")
    if not isinstance(flag, bool):
        raise PortalError(400, "Send enabled as true or false.")
    auth_now = enable_app(item) if flag else disable_app(item)
    return 200, {"enabled": bool(auth_now.get("enabled")), "callback": auth_now.get("callback"), "logout": auth_now.get("logout"), **_addresses()}
