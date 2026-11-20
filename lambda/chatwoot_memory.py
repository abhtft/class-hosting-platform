"""Conversation memory for the Chatwoot adapter: the last few turns of each conversation, kept for a limited time so the bot can follow a conversation.

A turn is what the customer wrote and what the bot answered. The worker reads the turns before it answers and writes the new turn after the reply has been posted:

    turns = load(store, agent_id, conversation_id, now)             -> [{"c": customer text, "b": bot text, "f": 1 when the bot's reply was its fallback}, ...] (oldest first)
    remember(store, agent_id, conversation_id, customer, bot, fallback, now)

Rules:
  * It is an OWN table (ChatwootMemoryTable) with an expiry and no point-in-time recovery, so the customer's words are not kept in backups. Nothing else holds them:
    never a message item, a log line, a metric or an event.
  * The time kept (chatwoot.memoryHours), the number of turns (chatwoot.memoryTurns) and the length of each text (chatwoot.memoryMessageChars) are settings.
    memoryHours 0 switches memory off: nothing is read and nothing is stored.
  * DynamoDB removes an expired item late (up to about two days), so an item past its expiry is treated as empty here and replaced by the next write.
  * Two messages of one conversation answered at the same moment can lose a turn (the write re-reads just before it writes, which keeps that rare). That is accepted.
  * A memory that cannot be read or written never stops an answer: the caller treats an error as "no memory".
"""
import platform_settings

HOURS_SECONDS = platform_settings.hours("chatwoot.memoryHours")  # 0 = memory off
TURNS = platform_settings.value("chatwoot.memoryTurns")  # how many (customer, bot) turns are kept
MESSAGE_CHARS = platform_settings.value("chatwoot.memoryMessageChars")  # each text is cut to this many characters


def enabled():
    return HOURS_SECONDS > 0


def key(agent_id, conversation_id):
    return f"mem#{agent_id}#{conversation_id}"


def cut(text):
    """One text as it is kept: plain, trimmed, cut to the setting."""
    return str(text or "").replace("\r", "").strip()[:MESSAGE_CHARS]


def clean(turns):
    """The turns in an item, checked: only well-formed ones, at most TURNS of them, newest last. Anything odd is dropped, never trusted."""
    out = []
    for turn in turns if isinstance(turns, list) else []:
        if isinstance(turn, dict) and isinstance(turn.get("c"), str) and isinstance(turn.get("b"), str):
            out.append({"c": cut(turn["c"]), "b": cut(turn["b"]), **({"f": 1} if turn.get("f") == 1 else {})})
    return out[-TURNS:]


def load(store, agent_id, conversation_id, now):
    """The turns of a conversation, oldest first; [] when memory is off, there is none, or it has expired."""
    if not enabled():
        return []
    item = store.get(key(agent_id, conversation_id))
    if not isinstance(item, dict) or not isinstance(item.get("ttl"), int) or item["ttl"] <= now:
        return []
    return clean(item.get("turns"))


def remember(store, agent_id, conversation_id, customer, bot, fallback, now):
    """Add one turn and keep the last TURNS. Re-reads just before writing, so a turn written by a message answered at the same moment is not thrown away."""
    if not enabled():
        return
    turns = load(store, agent_id, conversation_id, now)
    turns.append({"c": cut(customer), "b": cut(bot), **({"f": 1} if fallback else {})})
    store.put(key(agent_id, conversation_id), {"turns": turns[-TURNS:], "ttl": now + HOURS_SECONDS})


def last_customer(turns):
    """What the customer wrote last time (for the book search), or ''."""
    return turns[-1]["c"] if turns else ""


def stuck(turns, count=2):
    """True when the last `count` turns all ended in the fallback: the bot is not getting anywhere (used by the hand-over rules)."""
    return len(turns) >= count and all(turn.get("f") == 1 for turn in turns[-count:])


class DynamoMemory:
    """The memory table: items with a partition key `pk`, the turns and an expiry `ttl` (the table removes them by itself)."""

    def __init__(self, table):
        self.table = table

    def get(self, key_):
        item = self.table.get_item(Key={"pk": key_}).get("Item")
        return {"turns": item.get("turns"), "ttl": int(item["ttl"])} if item and "ttl" in item else None

    def put(self, key_, item):
        self.table.put_item(Item={"pk": key_, "turns": item["turns"], "ttl": item["ttl"]})


class MemoryOnly:
    """The same in a dict, for tests."""

    def __init__(self):
        self.items = {}

    def get(self, key_):
        return self.items.get(key_)

    def put(self, key_, item):
        self.items[key_] = item
