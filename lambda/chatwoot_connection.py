"""Connect an agent to Chatwoot from Studio: the three values Chatwoot gives (the bot secret, the bot's access token, the account number) are typed in Studio and kept in the
agent's secret `chatwoot/<agent>` in Secrets Manager, the same secret the receiver (bot secret) and the worker (token, account number) already read. Pure rules here; the vault
(Secrets Manager) is injected, so the tests run on a dictionary.

  Studio card  --PUT /admin/agents/<name>/connection-->  validate (clean)  -->  merge into what is stored (merge)  -->  vault.write
               <--GET ... {"bot_secret": "set"|"missing", ...}  (status: a value is NEVER returned)

Rules this module keeps: a value is never in a return, a log line or an error message (an error names the field, not what was typed); an empty or missing field keeps what is stored; the
checks are about the SHAPE of what was pasted (no spaces, a sane length), because a wrong paste is the usual mistake and it otherwise shows up only as a bot that stays silent."""
import json
import re

import chatwoot_registry as reg

SECRET_PREFIX = "chatwoot/"  # the secret of agent <id> is chatwoot/<id> (chatwoot_api reads exactly this name)
VALUE_FIELDS = {"bot_secret": "The bot secret", "api_token": "The bot access token"}
_VALUE = re.compile(r"[\x21-\x7e]{8,512}")  # printable ASCII without spaces: what Chatwoot shows; a pasted space or line break is the usual mistake
_BY = re.compile(r"[^\x00-\x1f\x7f]{1,120}")


def secret_name(agent):
    return SECRET_PREFIX + agent


def clean(body):
    """The changes in a request: {"bot_secret"?, "api_token"?, "account_id"?, "by"?}, checked. A missing, empty or blank value means 'keep what is stored' and is left out.
    Raises chatwoot_registry.Invalid (a 400) naming the field, never the value."""
    if not isinstance(body, dict):
        raise reg.Invalid("Send a JSON object.")
    extra = sorted(str(k) for k in set(body) - {*VALUE_FIELDS, "account_id", "by"})
    if extra:
        raise reg.Invalid("Unknown field: " + ", ".join(extra[:5]) + ". The fields are bot_secret, api_token and account_id.")
    out = {}
    for name, label in VALUE_FIELDS.items():
        value = body.get(name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise reg.Invalid(f"{label} must be text.")
        value = value.strip()
        if not value:
            continue
        if not _VALUE.fullmatch(value):
            raise reg.Invalid(f"{label} must be 8 to 512 letters, digits or signs with no spaces or line breaks. Check that you copied all of it and nothing else.")
        out[name] = value
    account = body.get("account_id")
    if isinstance(account, str):
        account = account.strip()
        account = int(account) if account.isdigit() and len(account) <= 9 else (None if not account else account)
    if account is not None:
        if not reg._whole(account) or account > 999_999_999:
            raise reg.Invalid("The account number must be a whole number above zero (in Chatwoot's address: /app/accounts/<number>/).")
        out["account_id"] = account
    by = body.get("by")
    if by is not None and not (isinstance(by, str) and _BY.fullmatch(by.strip())):
        raise reg.Invalid("by must be short text.")
    if isinstance(by, str):
        out["by"] = by.strip()
    if not {"bot_secret", "api_token", "account_id"} & set(out):
        raise reg.Invalid("Nothing to save: give at least one of bot_secret, api_token and account_id.")
    return out


def parse(text):
    """The stored secret as a dict ({} when there is none or it is not a JSON object)."""
    try:
        data = json.loads(text or "")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def merge(text, changes, default_account=None):
    """The new text of the secret: what was stored (unknown fields kept) with the changed fields replaced. With no account number anywhere, the agent's own is used."""
    data = parse(text)
    for name in ("bot_secret", "api_token", "account_id"):
        if name in changes:
            data[name] = changes[name]
    if not reg._whole(data.get("account_id")) and reg._whole(default_account):
        data["account_id"] = default_account
    return json.dumps(data, separators=(",", ":"))


def _has(value):
    return isinstance(value, str) and bool(value)


def status(agent, text):
    """What Studio may know about the connection: which values are set (never what they are), the account number, and one plain sentence when something is missing or wrong."""
    data = parse(text)
    secret_account = data.get("account_id") if reg._whole(data.get("account_id")) else None
    shown = {"bot_secret": "set" if _has(data.get("bot_secret")) else "missing", "api_token": "set" if _has(data.get("api_token")) else "missing",
             "account_id": secret_account, "agent_account_id": agent.get("account_id")}
    missing = [label.lower() for key, label in VALUE_FIELDS.items() if shown[key] == "missing"]
    if secret_account is None:
        missing.append("the account number")
    problem = None
    if missing:
        problem = "Not connected: " + ", ".join(missing) + (" is" if len(missing) == 1 else " are") + " missing."
    elif secret_account != agent.get("account_id"):
        problem = "The account number here and the one saved with the agent differ. Save the account number again."
    shown["connected"] = problem is None
    shown["problem"] = problem
    shown["changed_at"] = (agent.get("connection") or {}).get("changed_at")
    shown["changed_by"] = (agent.get("connection") or {}).get("changed_by")
    return shown


class SecretsVault:
    """Secrets Manager, the real one: read(name) -> text or None; write(name, text) creates the secret or puts a new value. Nothing here logs or returns a value."""

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("secretsmanager")
        return self._client

    @staticmethod
    def _code(error):
        return getattr(error, "response", {}).get("Error", {}).get("Code")

    def read(self, name):
        try:
            return self.client.get_secret_value(SecretId=name).get("SecretString")
        except Exception as error:
            if self._code(error) == "ResourceNotFoundException":
                return None
            raise

    def write(self, name, text):
        try:
            self.client.put_secret_value(SecretId=name, SecretString=text)
        except Exception as error:
            if self._code(error) != "ResourceNotFoundException":
                raise
            try:
                self.client.create_secret(Name=name, SecretString=text, Description="Chatwoot connection of an agent (bot secret, bot token, account number); written by Studio")
            except Exception as again:
                if self._code(again) != "ResourceExistsException":  # made by another call at the same moment: put the value in it
                    raise
                self.client.put_secret_value(SecretId=name, SecretString=text)
