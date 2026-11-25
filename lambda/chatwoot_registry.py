"""Chatwoot adapter registry, the pure part: who owns an agent, which Chatwoot account and inboxes it may serve, its versions, and the service key a tenant's
Studio uses. No AWS here: the stores are in chatwoot_store.py, the HTTP routes in chatwoot_admin.py, the webhook in chatwoot_api.py.

  tenant   a business owner. One item `tenant#<id>` holds the hash of its service key (never the key), whether it is enabled, and the names of its agents.
  agent    `agent#<name>`: its tenant, ONE Chatwoot account, a list of inboxes, enabled or not, how many versions exist, which one is live.
  version  `agent#<name>#v<n>`: an immutable copy of the configuration Studio published (the platform stores it untouched; the runtime of a later step reads it).

check_mapping() is the rule the receiver and the worker apply: a message is served only when the agent and its tenant are enabled and the message's Chatwoot
account is the agent's account and its inbox is one of the agent's inboxes. The service key is `cs_<tenant id>.<secret>`: only the SHA-256 of the secret is kept."""
import hashlib
import hmac
import json
import re
import secrets
import time

import chatwoot_core as core
import platform_settings

CACHE_SECONDS = platform_settings.value("chatwoot.registryCacheSeconds")
MAX_VERSION_BYTES = platform_settings.kb("chatwoot.maxVersionKB")
MAX_VERSIONS = platform_settings.value("chatwoot.maxVersions")
MAX_AGENTS = platform_settings.value("chatwoot.maxAgentsPerTenant")
MAX_INBOXES = platform_settings.value("chatwoot.maxInboxesPerAgent")
TENANT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,29}")  # always used with fullmatch
_LABEL = re.compile(r"[a-z][a-z_]{0,39}")  # a short lowercase word such as chatbot or lead_collection: Studio's own words, the platform only stores them
_KEY = re.compile(r"cs_([a-z0-9][a-z0-9-]{0,29})\.([A-Za-z0-9_-]{32,64})")
AGENT_FIELDS = ("account_id", "inbox_ids", "enabled", "type", "purpose", "expected_rev", "archived")
_NO_KEY = hashlib.sha256(b"no such tenant").hexdigest()  # compared against when the tenant does not exist, so a wrong tenant and a wrong secret take the same path


class Invalid(ValueError):
    """A request the registry refuses; the text is safe to show the caller."""


def tenant_key(tenant_id):
    return f"tenant#{tenant_id}"


def agent_key(name):
    return f"agent#{name}"


def teach_key(agent, sid):
    return f"teach#{agent}#{sid}"


def teach_index_key(agent):
    return f"teachidx#{agent}"


def repeat_key(agent, version):
    return f"repeat#{agent}#v{version}"


def repeat_state_key(agent):
    return f"repeatstate#{agent}"


def routing_tests_key(agent):
    """The routing table of an orchestrator (its rows and their revision). Under `suite#` so the same narrow permissions that cover the suites cover it."""
    return f"suite#routing#{agent}"


def suite_key(agent):
    """The test suite of an agent (its cases and their revision)."""
    return f"suite#{agent}"


def suite_state_key(agent, which=None):
    """What the suite runs of an agent have found: the latest runs and, per published version, whether it passed. `which` "central" is the same for the platform's central safety suite."""
    return f"suitestate#{agent}#central" if which == "central" else f"suitestate#{agent}"


def central_suite_key():
    """The platform's central safety suite: one item the teacher loads, every business may read and none may change."""
    return "suitecentral#standard"


def suite_run_key(agent, run_id):
    return f"suiterun#{agent}#{run_id}"


def version_key(name, number):
    return f"agent#{name}#v{int(number)}"


# ---------- the service key ----------

def hash_secret(secret):
    return hashlib.sha256(secret.encode("utf8")).hexdigest()


def new_service_key(tenant_id):
    """(the key to hand to the tenant once, the hash to store). 32 random bytes; the key is never stored."""
    secret = secrets.token_urlsafe(32)
    return f"cs_{tenant_id}.{secret}", hash_secret(secret)


def parse_service_key(text):
    """(tenant id, secret) from 'cs_<tenant>.<secret>', or None."""
    found = _KEY.fullmatch(text or "")
    return (found.group(1), found.group(2)) if found else None


def key_matches(tenant, secret):
    """True when the secret is the tenant's current key and the tenant is enabled. Constant-time; the same work whether the tenant exists or not."""
    stored = tenant.get("key_hash") if isinstance(tenant, dict) and isinstance(tenant.get("key_hash"), str) else _NO_KEY
    same = hmac.compare_digest(hash_secret(secret).encode("ascii"), stored.encode("ascii", "replace"))
    return bool(same and isinstance(tenant, dict) and tenant.get("enabled") is True)


# ---------- validation ----------

def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def clean_agent(body, creating):
    """The agent settings in a request, checked: {account_id, inbox_ids, enabled, type, purpose} (only the ones given), or Invalid. When creating, the
    account and the inboxes are required."""
    if not isinstance(body, dict):
        raise Invalid("Send a JSON object.")
    unknown = sorted(set(body) - set(AGENT_FIELDS))
    if unknown:
        raise Invalid(f"Unknown field: {unknown[0]}.")
    out = {}
    if "account_id" in body:
        if not _whole(body["account_id"]):
            raise Invalid("account_id must be a whole number above zero (the Chatwoot account number).")
        out["account_id"] = body["account_id"]
    if "inbox_ids" in body:
        inboxes = body["inbox_ids"]
        if not isinstance(inboxes, list) or not 1 <= len(inboxes) <= MAX_INBOXES or not all(_whole(i) for i in inboxes) or len(set(inboxes)) != len(inboxes):
            raise Invalid(f"inbox_ids must be a list of 1 to {MAX_INBOXES} different whole numbers above zero.")
        out["inbox_ids"] = list(inboxes)
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise Invalid("enabled must be true or false.")
        out["enabled"] = body["enabled"]
    for field in ("type", "purpose"):
        if field in body:
            if not isinstance(body[field], str) or not _LABEL.fullmatch(body[field]):
                raise Invalid(f"{field} must be a short lowercase word (letters and underscores).")
            out[field] = body[field]
    if "archived" in body:
        if not isinstance(body["archived"], bool):
            raise Invalid("archived must be true (archive the agent) or false (restore it).")
        out["archived"] = body["archived"]
    if "expected_rev" in body and not (isinstance(body["expected_rev"], int) and not isinstance(body["expected_rev"], bool) and body["expected_rev"] >= 0):
        raise Invalid("expected_rev must be a whole number.")
    if creating and out.get("type") == "department":  # reached only through an orchestrator of the same business: no inbox, no webhook of its own
        if out.get("inbox_ids"):
            raise Invalid("A department agent has no inbox of its own: its orchestrator's number serves it.")
        if "account_id" not in out:
            raise Invalid("account_id is required to create an agent.")
        out["inbox_ids"] = []
    elif creating and ("account_id" not in out or "inbox_ids" not in out):
        raise Invalid("account_id and inbox_ids are required to create an agent.")
    return out


def clean_config(body):
    """The configuration of a version (a JSON object), or Invalid. Its size is limited; the platform does not look inside."""
    config = body.get("config") if isinstance(body, dict) else None
    if not isinstance(config, dict) or set(body) != {"config"}:
        raise Invalid('Send {"config": {...}}: a JSON object and nothing else.')
    if len(json.dumps(config).encode("utf8")) > MAX_VERSION_BYTES:
        raise Invalid(f"The configuration is larger than {MAX_VERSION_BYTES // 1024} KB.")
    return config


# ---------- the rule the receiver and the worker apply ----------

def check_mapping(agent, tenant, account_id, inbox_id):
    """None when the message may be served, else 'not_mapped'. One reason for every cause, so nothing tells a caller why."""
    if not isinstance(agent, dict) or not isinstance(tenant, dict) or agent.get("tenant_id") != tenant.get("tenant_id"):
        return "not_mapped"
    if agent.get("enabled") is not True or tenant.get("enabled") is not True:
        return "not_mapped"
    if agent.get("account_id") != account_id or inbox_id not in (agent.get("inbox_ids") or []):
        return "not_mapped"
    return None


class Registry:
    """Reads the agent and its tenant (two single-item reads) and remembers them for a few seconds, so a busy inbox costs the table nothing extra. A missing
    agent is remembered too, for the same few seconds: an agent created a moment ago works after that. The worker uses ttl 0 and asks every time."""

    def __init__(self, store, ttl=None, clock=time.time):
        self.store, self.ttl, self.clock, self.cache = store, CACHE_SECONDS if ttl is None else ttl, clock, {}

    def load(self, name):
        now = self.clock()
        hit = self.cache.get(name)
        if hit and hit[0] > now:
            return hit[1]
        agent = self.store.get(agent_key(name))
        tenant = self.store.get(tenant_key(agent["tenant_id"])) if isinstance(agent, dict) and isinstance(agent.get("tenant_id"), str) else None
        if self.ttl > 0:
            self.cache[name] = (now + self.ttl, (agent, tenant))
        return agent, tenant

    def allows(self, name, account_id, inbox_id):
        agent, tenant = self.load(name)
        return check_mapping(agent, tenant, account_id, inbox_id)
