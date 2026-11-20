"""The sweeper (lambda/chatwoot_sweeper.py): it finds message items the worker started but never finished, marks each one failed (reason stuck) with a conditional write so one
item is swept once, and asks the worker with an event of ids only to hand the conversation to a person. A recent item, a finished one or one of another kind is left alone; a
worker that cannot be started is logged by error type; and the DynamoDB store sends exactly the scan and the conditional update the design relies on."""
import json
import os
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_api as api  # noqa: E402
import chatwoot_store as cs  # noqa: E402
import chatwoot_sweeper as sw  # noqa: E402

LOGS = []
sw.log = lambda what, **details: LOGS.append(json.dumps({"chatwoot_sweeper": what, **details}))
NOW = 1_800_000_000


def message(number, status, started, agent="shop", conversation=12):
    return {"pk": f"msg#{agent}#{number}", "agent_id": agent, "message_id": number, "conversation_id": conversation, "account_id": 1, "inbox_id": 3, "status": status, "started_at": started,
            "received_at": started - 1, "delivery_id": "d-1", "route": "rag"}


assert sw.STUCK_SECONDS == 300
store = cs.MemoryStore()
for item in (message(1, "processing", NOW - 301), message(2, "processing", NOW - 299), message(3, "replied", NOW - 5000), message(4, "failed", NOW - 5000), message(5, "received", NOW - 5000),
             message(6, "processing", NOW - 10000, agent="other", conversation=77)):
    store.items[item["pk"]] = item
store.items["agent#shop"] = {"pk": "agent#shop", "status": "processing", "started_at": 1}  # not a message item: never looked at
store.items["usage#acme#202611"] = {"pk": "usage#acme#202611", "micro": 5}
started = []
assert sw.handler({}, None, now=NOW, store=store, invoke=started.append) == {"handed": 2, "errors": 0}
assert sorted((job["agent_id"], job["message_id"]) for job in started) == [("other", 6), ("shop", 1)], "only what has been processing for more than 300 seconds"
assert started[0] == {"kind": "sweep", "agent_id": "other", "message_id": 6, "conversation_id": 77, "account_id": 1, "inbox_id": 3} or started[1] == {"kind": "sweep", "agent_id": "other", "message_id": 6, "conversation_id": 77, "account_id": 1, "inbox_id": 3}
for job in started:
    assert set(job) == {"kind", "agent_id", "message_id", "conversation_id", "account_id", "inbox_id"}, "ids only: no text, no secret"
assert store.items["msg#shop#1"]["status"] == "failed" and store.items["msg#shop#1"]["reason"] == "stuck" and store.items["msg#shop#1"]["finished_at"] == NOW
assert store.items["msg#shop#2"]["status"] == "processing" and store.items["msg#shop#3"]["status"] == "replied" and store.items["msg#shop#5"]["status"] == "received"
assert store.items["agent#shop"]["status"] == "processing" and "usage#acme#202611" in store.items
assert sw.handler({}, None, now=NOW + 1, store=store, invoke=started.append) == {"handed": 0, "errors": 0} and len(started) == 2, "an item is swept once"
assert sw.handler({}, None, now=NOW + 400, store=store, invoke=started.append) == {"handed": 1, "errors": 0} and started[-1]["message_id"] == 2, "and the one that was recent is swept when its time comes"
print("  ok  only message items that have been processing for too long are swept, once, with an event of ids only")


def boom(job):
    raise RuntimeError("throttled for 4111 Asha")


store = cs.MemoryStore()
store.items["msg#shop#1"] = message(1, "processing", NOW - 1000)
assert sw.handler({}, None, now=NOW, store=store, invoke=boom) == {"handed": 0, "errors": 1}
assert store.items["msg#shop#1"]["status"] == "failed" and "RuntimeError" in LOGS[-2] and "4111" not in "".join(LOGS), "a worker that cannot be started is logged by error type; the item stays failed"
print("  ok  a worker that cannot be started is logged by error type only")


# the real store: one scan (projected to ids, filtered to processing message items before the cutoff) and a conditional update
class FakeTable:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def scan(self, **args):
        self.calls.append(("scan", args))
        return self.pages.pop(0)

    def update_item(self, **args):
        self.calls.append(("update", args))
        if self.fail:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": cs.CONDITION_FAILED, "Message": "x"}}, "UpdateItem")

    fail = False


table = FakeTable([{"Items": [{"pk": "msg#shop#1", "agent_id": "shop", "message_id": 1}], "LastEvaluatedKey": {"pk": "msg#shop#1"}}, {"Items": [{"pk": "msg#shop#2", "agent_id": "shop", "message_id": 2}]}])
dynamo = cs.DynamoStore(table)
assert [item["pk"] for item in dynamo.stuck_messages(NOW)] == ["msg#shop#1", "msg#shop#2"], "every page is read"
first = table.calls[0][1]
assert first["ProjectionExpression"] == "pk, agent_id, message_id, conversation_id, account_id, inbox_id" and "ExclusiveStartKey" not in first and table.calls[1][1]["ExclusiveStartKey"] == {"pk": "msg#shop#1"}
assert "FilterExpression" in first
assert dynamo.mark_stuck("msg#shop#1", NOW) is True
update = table.calls[-1][1]
assert update["ConditionExpression"] == "#s = :processing" and update["ExpressionAttributeValues"][":failed"] == "failed" and update["ExpressionAttributeValues"][":stuck"] == "stuck" and update["Key"] == {"pk": "msg#shop#1"}
table.fail = True
assert dynamo.mark_stuck("msg#shop#1", NOW) is False, "a message that is no longer processing is not swept"
print("  ok  the DynamoDB store scans with a projection to ids and pages through, and marks an item stuck only if it is still processing")

print("test_chatwoot_sweeper.py: stuck message items")
