"""The janitor of the Chatwoot adapter's media: removes files that no published version uses any more, once a day.

A business's media files are counted in its ledger (media#<tenant>, see chatwoot_media.py). A file is `pending` from the moment its upload form is given and `kept` once a published
version lists it. The janitor reads, for each business, the media lists of every version that still exists (a retired version is gone), and:
  * a file that a version lists stays (and becomes `kept`);
  * a file that no version lists and whose last change is older than chatwoot.mediaPendingHours leaves the ledger (so it stops counting against the business) with a conditional write,
    and only then is its object deleted from S3.
A file that was uploaded a moment ago, or that a version being published has just claimed, is never touched, whatever else is true. If anything about a business cannot be read, that business
is skipped for the day: the janitor never deletes on a guess.

It needs the adapter's table (a scan for the media ledgers, single reads of agents and their versions, writes of ledgers) and the right to delete objects under chatwoot-media/. It never calls
Chatwoot, reads no secret and sees no customer words.
"""
import json
import os
import time

import chatwoot_media as media
import chatwoot_registry as reg


def log(what, **details):
    print(json.dumps({"chatwoot_janitor": what, **details}))


def default_store():
    import boto3
    from chatwoot_store import DynamoStore
    return DynamoStore(boto3.resource("dynamodb").Table(os.environ["CHATWOOT_TABLE"]))


def referenced_keys(store, ledger):
    """The files that any existing version of the agents named in the ledger lists. Raises when a version cannot be read."""
    keys = set()
    for agent_id in sorted({entry.get("agent") for entry in ledger.get("files", {}).values()}):
        agent = store.get(reg.agent_key(agent_id))
        for number in range(1, ((agent or {}).get("versions") or 0) + 1):
            version = store.get(reg.version_key(agent_id, number))  # a retired version is simply missing
            for item in ((version or {}).get("config") or {}).get("media") or []:
                if isinstance(item, dict) and item.get("key"):
                    keys.add(item["key"])
    return keys


def handler(event=None, context=None, now=None, store=None, files=None):
    now = int(now if now is not None else time.time())
    store = store or default_store()
    files = files or media.S3Files()
    removed = skipped = 0
    for ledger in store.list_prefix("media#"):
        tenant_id = ledger.get("tenant_id")
        try:
            gone = media.settle(store, tenant_id, referenced_keys(store, ledger), now)
            for key in gone:
                files.delete(key)  # after the ledger no longer lists it
            removed += len(gone)
        except Exception as error:  # a business that cannot be read or written today is left alone
            skipped += 1
            log("business_skipped", tenant=tenant_id, error=type(error).__name__)
    log("swept", removed=removed, skipped=skipped)
    return {"removed": removed, "skipped": skipped}
