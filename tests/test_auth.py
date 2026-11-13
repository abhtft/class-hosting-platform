"""Sign-in for student apps, platform side (lambda/auth.py, docs/AUTH_PLAN.md step A1).

Against in-memory stand-ins for Amazon Cognito and Systems Manager (the same approach as the backend tests):
the shared pool is made once; each app gets its own client with its own return address; the Google setting is read from
Systems Manager and never written anywhere; a missing or malformed setting is refused with a plain message; turning an app's
sign-in off removes only that app's client; the database never holds a client secret."""
import json
import os
import sys
import time

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E",
                  DISTRIBUTION_DOMAIN="d123.cloudfront.net", KEYS_PARAM="/k", ROOT_DOMAIN="example.com", BACKEND_ENABLED="true",
                  SANDBOX_ROLE_ARN="arn:aws:iam::1:role/sandbox", ORIGIN_SECRET="s3cr3t")
here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(here, "..", "lambda"), here]
import auth  # noqa: E402
import backends  # noqa: E402
import portal  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

portal._store = portal.MemoryStore()
db = portal.db


def fail(code, message="x"):
    return ClientError({"Error": {"Code": code, "Message": message}}, "Op")


class FakeSSM:
    def __init__(self, value=None):
        self.value = value
        self.reads = []

    def get_parameter(self, Name, WithDecryption):
        self.reads.append((Name, WithDecryption))
        if self.value is None:
            raise fail("ParameterNotFound")
        return {"Parameter": {"Value": self.value}}


class FakeCognito:
    def __init__(self):
        self.pools, self.domains, self.providers, self.clients, self.calls = {}, [], [], {}, []

    def create_user_pool(self, **kw):
        self.calls.append("create_user_pool")
        pool_id = f"eu-east-1_{len(self.pools) + 1}"
        self.pools[pool_id] = kw
        return {"UserPool": {"Id": pool_id}}

    def create_user_pool_domain(self, Domain, UserPoolId):
        self.calls.append("create_user_pool_domain")
        self.domains.append((Domain, UserPoolId))

    def create_identity_provider(self, **kw):
        self.calls.append("create_identity_provider")
        self.providers.append(kw)

    def create_user_pool_client(self, UserPoolId, **kw):
        self.calls.append("create_user_pool_client")
        client_id = f"client{len(self.clients) + 1}"
        self.clients[client_id] = {"UserPoolId": UserPoolId, **kw}
        return {"UserPoolClient": {"ClientId": client_id}}

    def delete_user_pool_client(self, UserPoolId, ClientId):
        self.calls.append("delete_user_pool_client")
        if ClientId not in self.clients:
            raise fail("ResourceNotFoundException")
        del self.clients[ClientId]


GOOD = json.dumps({"client_id": "123.apps.googleusercontent.com", "client_secret": "GOCSPX-secret-value"})
ssm = FakeSSM(GOOD)
cognito = FakeCognito()
backends._clients.update({"ssm": ssm, "cognito-idp": cognito})


def student(slug):
    db().put({"pk": f"tok#{slug}", "id": slug, "slug": slug})
    return db().get(f"tok#{slug}")


# ---- the Google setting: missing and malformed are refused, the secret is never echoed
ssm.value = None
a = student("ana")
try:
    auth.enable_app(a)
    raise AssertionError("missing Google setting must be refused")
except portal.PortalError as e:
    assert e.status == 503 and "not set up yet" in str(e), str(e)
assert cognito.calls == [], "nothing is created before the Google setting is there"
ssm.value = "not json"
try:
    auth.enable_app(a)
    raise AssertionError("malformed Google setting must be refused")
except portal.PortalError as e:
    assert e.status == 500 and "not valid" in str(e)
ssm.value = json.dumps({"client_id": "only-the-id"})
try:
    auth.google_credentials()
    raise AssertionError("a setting without the secret must be refused")
except portal.PortalError as e:
    assert e.status == 500
ssm.value = GOOD
print("  ok  the Google setting: missing or malformed is refused with a plain message, and nothing is created")

# ---- the shared pool is made once; the first app creates it, the second app only gets a client
first = auth.enable_app(student("ana"))
assert first["enabled"] is True and first["client_id"] == "client1"
assert first["callback"] == "https://ana.example.com/api/auth/callback", first["callback"]
assert first["logout"] == "https://ana.example.com/"
assert cognito.calls == ["create_user_pool", "create_user_pool_domain", "create_identity_provider", "create_user_pool_client"], cognito.calls
pool_id = first["pool_id"]
pool_settings = cognito.pools[pool_id]
assert pool_settings["UsernameAttributes"] == ["email"] and pool_settings["MfaConfiguration"] == "OFF"
assert len(cognito.providers) == 1 and cognito.providers[0]["ProviderName"] == "Google"
assert cognito.providers[0]["AttributeMapping"]["username"] == "sub", "the stable Google id is the user id"

second = auth.enable_app(student("ben"))
assert second["pool_id"] == pool_id, "both apps share one pool"
assert len(cognito.pools) == 1 and len(cognito.providers) == 1 and len(cognito.domains) == 1, "the pool, its domain and Google are made once"
assert second["callback"] == "https://ben.example.com/api/auth/callback"
assert cognito.clients["client2"]["CallbackURLs"] == ["https://ben.example.com/api/auth/callback"], "each app has its own return address"
assert cognito.clients["client1"]["CallbackURLs"] == ["https://ana.example.com/api/auth/callback"]
assert cognito.clients["client2"]["GenerateSecret"] is False, "a public client with PKCE: no secret to store"
assert cognito.clients["client2"]["AllowedOAuthFlows"] == ["code"] and "openid" in cognito.clients["client2"]["AllowedOAuthScopes"]
print("  ok  one shared pool and one Google registration for every app; each app has its own client and return address")

# ---- asking again changes nothing
assert auth.enable_app(db().get("tok#ana")) is not None
again = auth.enable_app(db().get("tok#ana"))
assert again["client_id"] == "client1" and len(cognito.clients) == 2
print("  ok  enabling an app that already has sign-in changes nothing")

# ---- the database never holds a secret
stored = json.dumps(db().get("tok#ana")) + json.dumps(db().get(auth.PLATFORM_KEY))
assert "GOCSPX" not in stored and "secret" not in stored.lower(), "no Google secret in the database"
assert len(db().get("tok#ana")["auth"]["session_key"]) >= 40, "each app has its own random session key"
assert db().get("tok#ana")["auth"]["session_key"] != db().get("tok#ben")["auth"]["session_key"], "the keys differ per app"
print("  ok  the database holds no Google secret; each app has its own random session key")

# ---- a run that stopped part way is finished by the next run, without a second pool
cognito2 = FakeCognito()
backends._clients["cognito-idp"] = cognito2
db().update(auth.PLATFORM_KEY, {"domain_made": False, "google_made": False})  # pretend the last run stopped before these two steps
ssm.value = GOOD
rec = auth.platform_pool()
assert rec["domain_made"] and rec["google_made"]
assert cognito2.calls == ["create_user_pool_domain", "create_identity_provider"], cognito2.calls
assert len(cognito2.pools) == 0, "the pool already existed: nothing is created twice"
backends._clients["cognito-idp"] = cognito
print("  ok  a run that stopped part way is finished by the next run; the pool is not made twice")

# ---- turning sign-in off removes only that app's client
off = auth.disable_app(db().get("tok#ben"))
assert off == {"enabled": False}
assert "client2" not in cognito.clients and "client1" in cognito.clients, "only ben's client is removed"
assert db().get("tok#ben")["auth"] == {"enabled": False}
assert auth.disable_app(db().get("tok#ben")) == {"enabled": False}, "turning it off twice is harmless"
gone = student("cy")
assert auth.disable_app(gone) == {"enabled": False}, "an app that never had sign-in is simply off"
print("  ok  turning sign-in off removes only that app's client; doing it twice or for an app without it is harmless")

# ---- enable again after off: a new client for that app only
back = auth.enable_app(db().get("tok#ben"))
assert back["enabled"] and back["client_id"] not in ("client1",) and back["pool_id"] == pool_id
print("  ok  turning sign-in back on gives the app a fresh client in the same pool")

# ---- a return address is always built from the platform's own domain
assert auth.app_base("ana") == "https://ana.example.com"
os.environ.pop("ROOT_DOMAIN")
assert auth.app_base("ana") == "https://d123.cloudfront.net/ana"
os.environ["ROOT_DOMAIN"] = "example.com"
print("  ok  return addresses follow the platform's rule: the app's own address, on the platform's domain")

# ---- the student's route: /api/student/sign-in (A2, part 1)
ssm.value = GOOD
cara = student("cara")
try:
    auth.student_route(cara, "POST", {"enabled": True})
    raise AssertionError("sign-in must need the backend")
except portal.PortalError as e:
    assert e.status == 403 and "backend" in str(e)
db().update(cara["pk"], {"backend": {"enabled": True}})
cara = db().get("tok#cara")
code, out = auth.student_route(cara, "POST", {"enabled": True})
assert code == 200 and out["enabled"] is True
assert out["callback"] == "https://cara.example.com/api/auth/callback"
assert out["google_return_address"].endswith(".auth.us-east-1.amazoncognito.com/oauth2/idpresponse"), out
assert out["google_return_address"].startswith("https://sd-class-"), "the return address is the pool's own domain"
assert out["login_host"] == out["google_return_address"].rsplit("/oauth2/idpresponse", 1)[0]
code, now_state = auth.student_route(db().get("tok#cara"), "GET", None)
assert code == 200 and now_state["enabled"] is True and now_state["google_return_address"] == out["google_return_address"]
code, off_state = auth.student_route(db().get("tok#cara"), "POST", {"enabled": False})
assert code == 200 and off_state["enabled"] is False
assert "callback" in off_state
for bad in (None, {}, {"enabled": "yes"}, {"enabled": 1}):
    try:
        auth.student_route(db().get("tok#cara"), "POST", bad)
        raise AssertionError(f"a bad body must be refused: {bad}")
    except portal.PortalError as e:
        assert e.status == 400
try:
    auth.student_route(db().get("tok#cara"), "DELETE", None)
    raise AssertionError("other methods are refused")
except portal.PortalError as e:
    assert e.status == 405
print("  ok  the student's sign-in route: refused without the backend, turns sign-in on and off, shows the return addresses to register, refuses bad bodies")

# ---- the app's function gets its sign-in settings, and loses them when sign-in is turned off
class FakeFunctions:
    def __init__(self):
        self.env = {"sd-dana": {"MY_OWN": "1", "CLASS_AUTH_HOST": "stale"}}

    def get_function_configuration(self, FunctionName):
        return {"Environment": {"Variables": dict(self.env[FunctionName])}}

    def update_function_configuration(self, FunctionName, Environment, **kw):
        self.env[FunctionName] = dict(Environment["Variables"])


functions = FakeFunctions()
backends._clients["lambda"] = functions
dana = student("dana")
db().update(dana["pk"], {"backend": {"enabled": True, "function_name": "sd-dana", "state": "ready"}})
code, out = auth.student_route(db().get("tok#dana"), "POST", {"enabled": True})
assert code == 200 and "session_key" not in json.dumps(out), "the route never shows the session key"
env = functions.env["sd-dana"]
assert env["MY_OWN"] == "1", "the student's own variables are kept"
assert env["CLASS_AUTH_HOST"].startswith("https://sd-class-") and env["CLASS_AUTH_HOST"].endswith(".auth.us-east-1.amazoncognito.com")
assert env["CLASS_AUTH_ISSUER"] == "https://cognito-idp.us-east-1.amazonaws.com/" + db().get("tok#dana")["auth"]["pool_id"]
assert env["CLASS_AUTH_REDIRECT"] == "https://dana.example.com/api/auth/callback" and env["CLASS_AUTH_HOME"] == "https://dana.example.com/"
assert env["CLASS_AUTH_CLIENT_ID"] == db().get("tok#dana")["auth"]["client_id"] and env["CLASS_AUTH_DAYS"] == "7"
assert env["CLASS_AUTH_KEY"] == db().get("tok#dana")["auth"]["session_key"]
assert set(db().get("tok#dana")["backend"]["env_names"]) >= set(auth.ENV_NAMES)
assert auth.env_for(db().get("tok#dana")) == {k: v for k, v in env.items() if k.startswith("CLASS_AUTH_")}, "a republish carries the same values"
for name in auth.ENV_NAMES:
    assert auth.reserved_name(db().get("tok#dana"), name)
db().update("tok#dana", {"expires_at": int(time.time()) + 86400, "created_at": int(time.time()) - 60})
try:
    backends.set_env(db().get("tok#dana"), "CLASS_AUTH_KEY", "mine", int(time.time()))
    raise AssertionError("a student may not set a sign-in value")
except portal.PortalError as e:
    assert e.status in (400, 403), e.status
auth.student_route(db().get("tok#dana"), "POST", {"enabled": False})
assert not any(k.startswith("CLASS_AUTH_") for k in functions.env["sd-dana"]) and functions.env["sd-dana"]["MY_OWN"] == "1"
assert auth.env_for(db().get("tok#dana")) == {}
print("  ok  the app's function gets the sign-in settings (and keeps the student's own variables); a student cannot set them; turning sign-in off removes them")

# ---- the gateway passes the visitor's cookies to the app
import gateway  # noqa: E402
ev = gateway.student_event({"cookies": ["class_session=abc", "other=1"], "requestContext": {"http": {"method": "GET"}}}, {"x-origin-secret": "s", "accept": "*/*"}, "/auth/me", "1.2.3.4")
assert ev["cookies"] == ["class_session=abc", "other=1"] and "x-origin-secret" not in ev["headers"]
assert "cookies" not in gateway.student_event({"requestContext": {"http": {}}}, {}, "/x", "1.2.3.4")
print("  ok  the gateway passes the visitor's cookies to the app's function")

print("test_auth.py: sign-in platform side (shared pool, per-app clients, Google setting, off and on) passes")
