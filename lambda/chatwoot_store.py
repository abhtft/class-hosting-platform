"""The one DynamoDB table behind the Chatwoot adapter (ChatwootTable, string key "pk"), and an in-memory twin for tests. Two kinds of items live in it:

  msg#<agent name>#<message id>        one per accepted message: ids, status and times (expires by itself, ttl)          written by chatwoot_api.py
  tenant#<id>, agent#<name>, agent#<name>#v<n>   the registry: who owns an agent, which Chatwoot account and inboxes it may serve, its versions   written by chatwoot_admin.py

Every write that must not overwrite is a conditional write: a message is claimed once (attribute_not_exists), a registry item is replaced only if its `rev`
still has the value that was read (optimistic concurrency: two callers cannot silently overwrite each other)."""
import json
from decimal import Decimal

CONDITION_FAILED = "ConditionalCheckFailedException"


def plain(value):
    """What DynamoDB returns (numbers as Decimal) as ordinary Python numbers."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    return value


def storable(item):
    """The item as DynamoDB takes it (it refuses Python floats): numbers with a fraction become Decimal."""
    return json.loads(json.dumps(item), parse_float=Decimal)


class DynamoStore:
    def __init__(self, table):
        self.table = table

    def _conditional(self, **args):
        """Run an update or put whose condition may fail: True when it was applied, False when the condition was not met."""
        from botocore.exceptions import ClientError
        try:
            args.pop("kind")(**args)
        except ClientError as error:
            if error.response["Error"]["Code"] == CONDITION_FAILED:
                return False
            raise
        return True

    def get(self, key):
        item = self.table.get_item(Key={"pk": key}).get("Item")
        return plain(item) if item else None

    def claim(self, key, item):
        """Write the item only if there is none yet. True for the first write, False for a repeat."""
        return self._conditional(kind=self.table.put_item, Item={**storable(item), "pk": key}, ConditionExpression="attribute_not_exists(pk)")

    def replace_if(self, key, item, field, expected):
        """Replace the item only if its `field` still equals `expected`. True when replaced."""
        return self._conditional(kind=self.table.put_item, Item={**storable(item), "pk": key}, ConditionExpression="#f = :e",
                                 ExpressionAttributeNames={"#f": field}, ExpressionAttributeValues={":e": expected})

    def note_duplicate(self, key, now):
        self._conditional(kind=self.table.update_item, Key={"pk": key}, UpdateExpression="ADD #d :one SET #t = :now", ConditionExpression="attribute_exists(pk)",
                          ExpressionAttributeNames={"#d": "duplicates", "#t": "last_duplicate_at"}, ExpressionAttributeValues={":one": 1, ":now": now})

    def forget(self, key):
        self.table.delete_item(Key={"pk": key})

    def begin(self, key, now):
        """Claim the message for the worker: only one worker run may move it from received to processing."""
        return self._conditional(kind=self.table.update_item, Key={"pk": key}, UpdateExpression="SET #s = :processing, #t = :now", ConditionExpression="#s = :received",
                                 ExpressionAttributeNames={"#s": "status", "#t": "started_at"},
                                 ExpressionAttributeValues={":processing": "processing", ":received": "received", ":now": now})

    def finish(self, key, status, now, extra=None):
        """Record the outcome (replied or failed) with a few more plain fields (the reply's id, a reason code)."""
        names, values, sets = {"#s": "status", "#t": "finished_at"}, {":status": status, ":now": now}, ["#s = :status", "#t = :now"]
        for number, (name, value) in enumerate(sorted((extra or {}).items())):
            names[f"#x{number}"], values[f":x{number}"] = name, value
            sets.append(f"#x{number} = :x{number}")
        self._conditional(kind=self.table.update_item, Key={"pk": key}, UpdateExpression="SET " + ", ".join(sets), ConditionExpression="attribute_exists(pk)",
                          ExpressionAttributeNames=names, ExpressionAttributeValues=values)

    def usage(self, key):
        """What has been spent so far under this counter (micro-dollars)."""
        item = self.table.get_item(Key={"pk": key}).get("Item")
        return int(item["micro"]) if item and "micro" in item else 0

    def usage_add(self, key, micro, now):
        """Add to the counter atomically (it expires by itself after two months). Returns the new total."""
        result = self.table.update_item(Key={"pk": key}, UpdateExpression="ADD #m :n SET #t = :ttl", ExpressionAttributeNames={"#m": "micro", "#t": "ttl"},
                                        ExpressionAttributeValues={":n": int(micro), ":ttl": now + 62 * 86400}, ReturnValues="UPDATED_NEW")
        return int(result["Attributes"]["micro"])

    def stuck_messages(self, cutoff):
        """The message items still `processing` that started before the cutoff (ids only). A scan of the adapter's own small table, projected to the ids; only the sweeper
        calls it."""
        from boto3.dynamodb.conditions import Attr
        items, args = [], {"FilterExpression": Attr("pk").begins_with("msg#") & Attr("status").eq("processing") & Attr("started_at").lt(cutoff),
                           "ProjectionExpression": "pk, agent_id, message_id, conversation_id, account_id, inbox_id"}
        while True:
            page = self.table.scan(**args)
            items.extend(plain(item) for item in page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                return items
            args["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def mark_stuck(self, key, now):
        """Move a message from processing to failed (reason stuck): True for the one sweep that did it, False when it was no longer processing."""
        return self._conditional(kind=self.table.update_item, Key={"pk": key}, UpdateExpression="SET #s = :failed, #t = :now, #r = :stuck", ConditionExpression="#s = :processing",
                                 ExpressionAttributeNames={"#s": "status", "#t": "finished_at", "#r": "reason"},
                                 ExpressionAttributeValues={":failed": "failed", ":processing": "processing", ":now": now, ":stuck": "stuck"})

    def list_prefix(self, prefix):
        """Every item whose key starts with the prefix. A full scan: only the teacher's local command (tools/chatwoot_admin.py) uses it, never a function."""
        from boto3.dynamodb.conditions import Attr
        items, args = [], {"FilterExpression": Attr("pk").begins_with(prefix)}
        while True:
            page = self.table.scan(**args)
            items.extend(plain(item) for item in page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                return items
            args["ExclusiveStartKey"] = page["LastEvaluatedKey"]


class MemoryStore:
    """Same interface as DynamoStore; used by tests and by the local command's tests."""

    def __init__(self):
        self.items = {}

    def get(self, key):
        return json.loads(json.dumps(self.items[key])) if key in self.items else None

    def claim(self, key, item):
        if key in self.items:
            return False
        self.items[key] = json.loads(json.dumps({**item, "pk": key}))
        return True

    def replace_if(self, key, item, field, expected):
        current = self.items.get(key)
        if current is None or current.get(field) != expected:
            return False
        self.items[key] = json.loads(json.dumps({**item, "pk": key}))
        return True

    def note_duplicate(self, key, now):
        if key in self.items:
            self.items[key]["duplicates"] = self.items[key].get("duplicates", 0) + 1
            self.items[key]["last_duplicate_at"] = now

    def forget(self, key):
        self.items.pop(key, None)

    def begin(self, key, now):
        item = self.items.get(key)
        if not item or item.get("status") != "received":
            return False
        item["status"], item["started_at"] = "processing", now
        return True

    def finish(self, key, status, now, extra=None):
        if key in self.items:
            self.items[key].update({"status": status, "finished_at": now, **(extra or {})})

    def usage(self, key):
        return int(self.items.get(key, {}).get("micro", 0))

    def usage_add(self, key, micro, now):
        item = self.items.setdefault(key, {"pk": key, "micro": 0})
        item["micro"] += int(micro)
        item["ttl"] = now + 62 * 86400
        return item["micro"]

    def stuck_messages(self, cutoff):
        names = ("pk", "agent_id", "message_id", "conversation_id", "account_id", "inbox_id")
        return [{name: item.get(name) for name in names} for key, item in sorted(self.items.items())
                if key.startswith("msg#") and item.get("status") == "processing" and isinstance(item.get("started_at"), int) and item["started_at"] < cutoff]

    def mark_stuck(self, key, now):
        item = self.items.get(key)
        if not item or item.get("status") != "processing":
            return False
        item.update({"status": "failed", "finished_at": now, "reason": "stuck"})
        return True

    def list_prefix(self, prefix):
        return [json.loads(json.dumps(item)) for key, item in sorted(self.items.items()) if key.startswith(prefix)]
