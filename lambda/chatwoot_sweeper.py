"""The sweeper of the Chatwoot adapter: finds message items the worker never finished and hands their conversations to a person.

A worker that dies between posting a reply and recording it (or is stopped by its time limit) leaves its message item in `processing` for ever, and the customer may have
heard nothing. Every few minutes this function looks for items that have been `processing` for longer than chatwoot.sweepStuckSeconds, marks each one `failed` (reason
`stuck`) with a conditional write (so one item is swept once, even if two sweeps overlap) and asks the worker, with an asynchronous event that holds ids only, to hand that
conversation to a person: a private note and `open`, but NO sentence to the customer (it is not known whether the reply was delivered).

It needs the message items of the adapter's table and the right to start the one worker. It never calls Chatwoot, reads no secret and no customer words (there are none in
a message item).
"""
import json
import os
import time

import chatwoot_api as api
import platform_settings

STUCK_SECONDS = platform_settings.value("chatwoot.sweepStuckSeconds")
IDS = ("agent_id", "message_id", "conversation_id", "account_id", "inbox_id")


def log(what, **details):
    print(json.dumps({"chatwoot_sweeper": what, **details}))


def default_store():
    import boto3
    return api.DynamoStore(boto3.resource("dynamodb").Table(os.environ["CHATWOOT_TABLE"]))


def handler(event=None, context=None, now=None, store=None, invoke=None):
    now = int(now if now is not None else time.time())
    store = store or default_store()
    invoke = invoke or api.invoke_worker
    cutoff = now - STUCK_SECONDS
    handed = errors = 0
    for item in store.stuck_messages(cutoff):
        if not store.mark_stuck(item["pk"], now):  # another sweep got there first, or the worker finished meanwhile
            continue
        job = {"kind": "sweep", **{name: item.get(name) for name in IDS}}
        try:
            invoke(job)
            handed += 1
        except Exception as error:  # the item stays `failed (stuck)`; the next sweep does not retry it: a person looks at the failed items
            errors += 1
            log("handover_not_started", error=type(error).__name__)
    log("swept", handed=handed, errors=errors)
    return {"handed": handed, "errors": errors}
