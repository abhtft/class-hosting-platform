"""Conversation memory (lambda/chatwoot_memory.py): the last turns of a conversation, kept for a limited time in their own table. A turn is cut to the setting's length,
only the last turns are kept, an expired item counts as empty, memory off (0 hours) reads and writes nothing, odd items are never trusted, and the DynamoDB wrapper
writes and reads exactly the items the table design has (a partition key `pk`, the turns and an expiry `ttl`)."""
import os
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_memory as mem  # noqa: E402

NOW = 1_800_000_000
store = mem.MemoryOnly()

assert mem.enabled() and mem.HOURS_SECONDS == 24 * 3600 and mem.TURNS == 6 and mem.MESSAGE_CHARS == 400, "the shipped defaults: 24 hours, 6 turns, 400 characters"
assert mem.key("shop", 12) == "mem#shop#12"

# a turn is kept, oldest first, cut to the setting
assert mem.load(store, "shop", 12, NOW) == []
mem.remember(store, "shop", 12, "  hello\r\n", "Namaste", False, NOW)
mem.remember(store, "shop", 12, "x" * 1000, "y" * 1000, True, NOW + 5)
turns = mem.load(store, "shop", 12, NOW + 6)
assert turns[0] == {"c": "hello", "b": "Namaste", "t": NOW} and turns[1] == {"c": "x" * 400, "b": "y" * 400, "f": 1, "t": NOW + 5}, "every turn carries the time it happened"
assert store.items["mem#shop#12"]["ttl"] == NOW + 5 + 24 * 3600
assert mem.last_customer(turns) == "x" * 400 and mem.last_customer([]) == ""
print("  ok  a turn is kept oldest first, cut to the length setting, and marked when the bot's answer was its fallback")

# only the last turns; another conversation or agent is another item
for number in range(10):
    mem.remember(store, "shop", 12, f"q{number}", f"a{number}", False, NOW + 10)
assert [t["c"] for t in mem.load(store, "shop", 12, NOW + 11)] == [f"q{n}" for n in range(4, 10)], "the last six"
assert mem.load(store, "shop", 13, NOW + 11) == [] and mem.load(store, "other", 12, NOW + 11) == []
print("  ok  only the last turns are kept, and a conversation only ever sees its own")

# the time of a turn is kept with or without a department (a greeting answered by the orchestrator's own books still has one), and an odd time is dropped
mem.remember(store, "shop", 20, "hi", "menu", False, NOW + 100)
mem.remember(store, "shop", 20, "1", "library", False, NOW + 160, department="library")
assert mem.load(store, "shop", 20, NOW + 200) == [{"c": "hi", "b": "menu", "t": NOW + 100}, {"c": "1", "b": "library", "t": NOW + 160, "d": "library"}]
store.items["mem#shop#21"] = {"turns": [{"c": "a", "b": "b", "t": "yesterday"}, {"c": "c", "b": "d", "t": True}], "ttl": NOW + 999}
assert mem.load(store, "shop", 21, NOW) == [{"c": "a", "b": "b"}, {"c": "c", "b": "d"}], "a time that is not a number is not trusted"
print("  ok  every turn carries its time (with or without a department), an odd time is dropped")

# expiry: an item past its time is empty (the table removes it late), and the next write replaces it
assert mem.load(store, "shop", 12, NOW + 10 + 24 * 3600 - 1) != [] and mem.load(store, "shop", 12, NOW + 10 + 24 * 3600) == []
mem.remember(store, "shop", 12, "new", "reply", False, NOW + 10 + 24 * 3600 + 5)
assert [t["c"] for t in mem.load(store, "shop", 12, NOW + 10 + 24 * 3600 + 6)] == ["new"]
print("  ok  an expired item counts as empty and the next write starts again")

# odd items are never trusted
for bad in (None, "text", {"turns": "x", "ttl": NOW + 99}, {"turns": [{"c": 1, "b": "x"}], "ttl": NOW + 99}, {"turns": [], "ttl": "soon"}, {"ttl": NOW + 99}):
    store.items["mem#shop#99"] = bad
    assert mem.load(store, "shop", 99, NOW) == [], bad
store.items["mem#shop#99"] = {"turns": ["junk", {"c": "ok", "b": "fine", "f": 7}, {"c": "a"}], "ttl": NOW + 99}
assert mem.load(store, "shop", 99, NOW) == [{"c": "ok", "b": "fine"}], "only well-formed turns, and only f=1 counts as a fallback"
print("  ok  an item that is not what was written is read as empty, and only well-formed turns are used")

# stuck: the last turns all ended in the fallback
fb = {"c": "?", "b": "welcome", "f": 1}
assert mem.stuck([fb, fb]) and not mem.stuck([fb]) and not mem.stuck([fb, {"c": "a", "b": "b"}]) and not mem.stuck([{"c": "a", "b": "b"}, fb]) and mem.stuck([fb], 1)
print("  ok  stuck() sees a bot that fell back twice in a row")

# memory off
saved = mem.HOURS_SECONDS
mem.HOURS_SECONDS = 0
off = mem.MemoryOnly()
mem.remember(off, "shop", 12, "hello", "hi", False, NOW)
assert off.items == {} and mem.load(store, "shop", 12, NOW) == [] and not mem.enabled()
mem.HOURS_SECONDS = saved
print("  ok  with 0 hours nothing is stored and nothing is read")


# the DynamoDB wrapper: one item per conversation, the expiry as a number, nothing else
class FakeTable:
    def __init__(self):
        self.items, self.calls = {}, []

    def get_item(self, Key):
        self.calls.append(("get", Key))
        item = self.items.get(Key["pk"])
        return {"Item": item} if item else {}

    def put_item(self, Item):
        self.calls.append(("put", sorted(Item)))
        self.items[Item["pk"]] = Item


table = FakeTable()
dynamo = mem.DynamoMemory(table)
mem.remember(dynamo, "shop", 12, "hello", "hi", False, NOW)
assert table.calls[-1] == ("put", ["pk", "ttl", "turns"]) and table.items["mem#shop#12"]["ttl"] == NOW + 24 * 3600
assert mem.load(dynamo, "shop", 12, NOW) == [{"c": "hello", "b": "hi", "t": NOW}]
assert dynamo.get("mem#shop#404") is None
table.items["mem#shop#5"] = {"pk": "mem#shop#5", "turns": []}
assert dynamo.get("mem#shop#5") is None, "an item without an expiry is ignored"
print("  ok  the DynamoDB wrapper writes one item per conversation (key, turns, expiry) and reads it back")

print("test_chatwoot_memory.py: conversation memory")
