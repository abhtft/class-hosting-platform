"""The media library of an agent (lambda/chatwoot_media.py and the places that use it): the list in a version is checked (kinds, sizes, own files only, https links), the model sees only the
descriptions and may end its answer with `MEDIA: id`, an unknown id or too many are dropped, the worker sends the text first and then each item (a real attachment, a link when the
attachment is refused, nothing twice after a timeout), a file that is not what its name says is never sent, a customer's own photo or voice note hands the conversation to a person,
Studio gets an upload form for an allowed type and size only, and the suite can check which media were sent."""
import json
import os
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_admin as admin  # noqa: E402
import chatwoot_api as api  # noqa: E402
import chatwoot_core as core  # noqa: E402
import chatwoot_handover as ho  # noqa: E402
import chatwoot_media as media  # noqa: E402
import chatwoot_memory as mem  # noqa: E402
import chatwoot_registry as reg  # noqa: E402
import chatwoot_runtime as rt  # noqa: E402
import chatwoot_suite as suite  # noqa: E402

LOGS = []
api.log = lambda what, **details: LOGS.append(json.dumps({"chatwoot": what, **details}))
admin.log = lambda what, **details: None
api.BASE_URL = "https://chat.example.org"
api.AUTH_STYLE = "bearer"
TOKEN = "test-only-token-abc123XYZ"
NOW = 1_800_000_000
KEY_IMG = "chatwoot-media/acme/shop/" + "a" * 32 + ".jpg"
KEY_PDF = "chatwoot-media/acme/shop/" + "b" * 32 + ".pdf"
KEY_OTHER = "chatwoot-media/other/shop/" + "c" * 32 + ".jpg"
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 20
PDF = b"%PDF-1.4 test"
OFFER = {"id": "offer", "name": "Diwali offer", "kind": "image", "description": "the photo of the current offer", "key": KEY_IMG, "size": len(JPEG)}
PRICES = {"id": "price_list", "name": "Price list", "kind": "document", "description": "the pdf with all prices", "key": KEY_PDF, "size": len(PDF)}
OFFICE = {"id": "office_map", "name": "Our office", "kind": "location", "description": "where the office is, for a customer who asks the way", "url": "https://maps.google.com/?q=office"}
BASE_CONFIG = {"language": "en", "collections": ["faq"], "fallback_text": "Sorry, a person will help you soon.", "max_answer_chars": 300, "mode": "rag"}
HIT = {"doc": "faq-doc", "title": "Faq", "page": 1, "page_end": 1, "section": "", "text": "We are in Delhi.", "vector": 0.9, "collection": "faq"}


def invalid(items, tenant="acme", agent="shop"):
    try:
        media.clean_items(items, tenant, agent)
    except media.Invalid as error:
        return str(error)
    raise AssertionError(f"{items} must be refused")


# ---- the list in a version
assert media.clean_items(None) == [] and media.clean_items([]) == []
assert media.clean_items([OFFER, PRICES, OFFICE], "acme", "shop") == [OFFER, PRICES, OFFICE]
assert "another agent" in invalid([{**OFFER, "key": KEY_OTHER}]), "a file of another business is not usable"
assert "uploaded for a image" in invalid([{**OFFER, "key": KEY_PDF}]) and "uploaded for a image" in invalid([{**OFFER, "key": "chatwoot-media/acme/shop/x.jpg"}])
assert "at most" in invalid([{**OFFER, "size": media.IMAGE_MAX_BYTES + 1}]) and "at most" in invalid([{**OFFER, "size": 0}])
assert "description is required" in invalid([{**OFFER, "description": ""}]) and "name must be" in invalid([{**OFFER, "name": "x" * (media.NAME_CHARS + 1)}])
assert "id must be" in invalid([OFFER, OFFER]) and "id must be" in invalid([{**OFFER, "id": "Bad Id"}]) and "unknown field" in invalid([{**OFFER, "extra": 1}])
assert "kind must be" in invalid([{**OFFER, "kind": "audio"}]) and "a web address, not a file" in invalid([{**OFFICE, "key": KEY_IMG}]) and "is a file" in invalid([{**OFFER, "url": "https://x.example"}])
for bad in ("http://maps.example/x", "https://127.0.0.1/x", "https://localhost/x", "https://user:pw@maps.example/x", "https://maps example.com", "javascript:alert(1)", "https://x", None, 5):
    invalid([{**OFFICE, "url": bad}])
assert len(invalid([OFFER] * 2)) > 0 and "at most" in invalid([{**OFFER, "id": f"m{i}"} for i in range(media.MAX_ITEMS + 1)])
print("  ok  the media list of a version: kinds, sizes, own files only, https addresses, different ids, nothing unknown")

# ---- what is a real file
assert media.looks_like(KEY_IMG, JPEG) and not media.looks_like(KEY_IMG, b"MZ\x90\x00 a program") and media.looks_like(KEY_PDF, PDF) and not media.looks_like(KEY_PDF, JPEG)
assert media.looks_like("chatwoot-media/a/b/" + "d" * 32 + ".mp4", b"\x00\x00\x00\x18ftypmp42") and not media.looks_like("chatwoot-media/a/b/" + "d" * 32 + ".mp4", b"\x00\x00\x00\x18nope")
assert media.looks_like("chatwoot-media/a/b/" + "e" * 32 + ".webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ") and not media.looks_like("chatwoot-media/a/b/" + "e" * 32 + ".webp", b"RIFF\x00\x00\x00\x00WAVE")
assert media.mime_of(KEY_IMG) == "image/jpeg" and media.file_name({"name": "Diwali offer / 2026!", "key": KEY_IMG}) == "Diwali_offer_2026.jpg" and media.file_name({"name": "///", "key": KEY_PDF}) == "file.pdf"
print("  ok  a file must look like its type (a renamed program is not a photo), with a safe name")

# ---- the model's side
assert media.prompt_part([]) == "" and "<media>" in media.prompt_part([OFFER]) and "[offer] Diwali offer: the photo" in media.prompt_part([OFFER, OFFICE]) and "MEDIA:" in media.prompt_part([OFFER])
text, picked = media.split_answer("We are at Nehru Place.\nMEDIA: office_map, offer, price_list, nothing, offer", [OFFER, PRICES, OFFICE])
assert text == "We are at Nehru Place." and [i["id"] for i in picked] == ["office_map", "offer", "price_list"][:media.MAX_PER_REPLY]
assert media.split_answer("Hello\nmedia: none", [OFFER]) == ("Hello", []) and media.split_answer("Hello", [OFFER]) == ("Hello", []) and media.split_answer("Hi\nMEDIA: ghost", [OFFER]) == ("Hi", [])
assert media.split_answer("Hi\nMEDIA: offer", None) == ("Hi", []), "an agent without media sends none, and the line is still never shown"
print("  ok  the model sees descriptions only; its MEDIA line is removed, unknown ids and more than the limit are dropped")

# ---- a version's configuration and the prompt
config = rt.clean_config({**BASE_CONFIG, "fallback_text": "Sorry.", "media": [OFFER, OFFICE]})
assert config["media"] == [OFFER, OFFICE] and "media" not in rt.clean_config({**BASE_CONFIG, "fallback_text": "Sorry."})
try:
    rt.clean_config({**BASE_CONFIG, "media": [{**OFFER, "kind": "audio"}]})
    raise AssertionError("a bad media list must be a ConfigError")
except rt.ConfigError:
    pass
messages, _ = rt.build_messages(config, "where are you", [HIT])
assert "<media>" in messages[0]["content"] and "[office_map]" in messages[0]["content"]
assert "<media>" not in rt.build_messages(rt.clean_config({**BASE_CONFIG, "fallback_text": "Sorry."}), "hi", [HIT])[0][0]["content"]


class Runtime:
    def __init__(self, answer):
        self.answer = answer
        self.search = lambda tenant, collections, query: {"hits": [HIT], "confident": True, "tokens": 5, "cost_micro": 1, "missing": []}
        self.llm = lambda messages, max_tokens, **kw: {"text": self.answer, "model": "m", "tokens": 10, "cost_micro": 2}
        self.agent = None


made = rt.respond(config, "where is your office", "acme", Runtime("We are in Delhi.\nMEDIA: office_map, ghost"))
assert made["route"] == "rag" and made["text"] == "We are in Delhi." and made["metrics"]["media"] == ["office_map"]
made = rt.respond(config, "hello", "acme", Runtime("We are in Delhi."))
assert "media" not in made["metrics"], "nothing is sent unless the model asks"
made = rt.respond(config, "what is the moon", "acme", Runtime(rt.NOT_SURE + "\nMEDIA: offer"))
assert made["route"] == "fallback" and "media" not in made["metrics"], "an answer of 'I do not know' sends no media"
print("  ok  the version's media list reaches the prompt, the answer loses the MEDIA line, and a fallback sends nothing")

# ---- Chatwoot: the file request and the customer's own files
url, headers, body = core.attachment_request("https://chat.example.org", "bearer", TOKEN, 1, 12, "offer.jpg", "image/jpeg", JPEG, "Diwali offer", boundary="BND")
assert url == "https://chat.example.org/api/v1/accounts/1/conversations/12/messages" and headers["Content-Type"] == "multipart/form-data; boundary=BND" and headers["Authorization"] == "Bearer " + TOKEN
assert b'name="attachments[]"; filename="offer.jpg"' in body and b"Content-Type: image/jpeg" in body and JPEG in body and body.endswith(b"--BND--\r\n") and b'name="private"\r\n\r\nfalse' in body
assert core.attachment_kinds({"attachments": [{"file_type": "image"}, {"file_type": "image"}, {"file_type": "audio"}, {"file_type": "Bad Type!"}, "x", {}]}) == ["image", "audio"]
assert core.attachment_kinds({}) == [] and core.attachment_kinds({"attachments": "image"}) == [] and len(core.attachment_kinds({"attachments": [{"file_type": c} for c in "abcdefgh"]})) == 5
assert ho.decide("", [], list(ho.TRIGGERS), ["image"]) == "customer_media" and ho.decide("", [], list(ho.TRIGGERS), []) is None and ho.decide("", [], ["stuck"], ["image"]) is None
assert ho.decide("this is my receipt", [], list(ho.TRIGGERS), ["image"]) is None, "a photo with words is answered from the words"
assert ho.decide("", [{"c": "x", "b": "y", "h": 1}], list(ho.TRIGGERS), ["image"]) is None, "a hand-over is not repeated"
print("  ok  the attachment request is a multipart Create New Message with the same token; a customer's file is noticed by kind only, and only a file without words hands over")


# ---- the worker sends the text, then each item
class Fakes:
    def __init__(self, llm_text, files=None, media_items=(OFFER, PRICES, OFFICE)):
        self.store = api.MemoryStore()
        self.requests, self.answers = [], []
        self.llm_text = llm_text
        self.files = media.MemoryFiles()
        self.files.put(KEY_IMG, JPEG)
        self.files.put(KEY_PDF, PDF)
        for key, data in (files or {}).items():
            self.files.put(key, data)
        self.store.claim(reg.tenant_key("acme"), {"tenant_id": "acme", "enabled": True, "key_hash": "x", "agents": ["shop"], "rev": 1})
        self.store.claim(reg.agent_key("shop"), {"tenant_id": "acme", "agent_id": "shop", "account_id": 1, "inbox_ids": [3], "enabled": True, "live_version": 1, "versions": 1, "rev": 1})
        self.store.claim(reg.version_key("shop", 1), {"agent_id": "shop", "tenant_id": "acme", "version": 1, "config": {**BASE_CONFIG, "media": list(media_items)}, "published_at": 1})
        self.runtime = rt.make_runtime(lambda t, c, q: {"hits": [HIT], "confident": True, "tokens": 5, "cost_micro": 1, "missing": []},
                                       lambda messages, max_tokens, **kw: {"text": self.llm_text, "model": "m", "tokens": 10, "cost_micro": 2})
        secrets = api.Secrets(lambda name: json.dumps({"bot_secret": "s", "api_token": TOKEN, "account_id": 1}), api.credentials_of)
        self.deps = api.Deps(self.store, secrets, http=self.post, registry=reg.Registry(self.store, ttl=0), runtime=self.runtime, memory=mem.MemoryOnly(), files=self.files)

    def post(self, url, headers, body, timeout):
        self.requests.append((url, headers, body, timeout))
        answer = self.answers.pop(0) if self.answers else (200, b'{"id": 9001}')
        if isinstance(answer, Exception):
            raise answer
        return answer


def asked(text="where are you", attachments=()):
    return {"agent_id": "shop", "message_id": 501, "conversation_id": 12, "account_id": 1, "inbox_id": 3, "text": text, "channel": "whatsapp", "attachments": list(attachments)}


def sent_text(request):
    return json.loads(request[2])["content"]


f = Fakes("We are at Nehru Place.\nMEDIA: office_map, offer")
out = api.answer(asked(), f.deps, NOW)
assert out["status"] == "replied" and out["metrics"]["media"] == [["office_map", "link"], ["offer", "attached"]], out["metrics"]
assert len(f.requests) == 3 and sent_text(f.requests[0]) == "We are at Nehru Place." and "MEDIA" not in sent_text(f.requests[0]), "the text goes first and never shows the MEDIA line"
assert sent_text(f.requests[1]) == "Our office: https://maps.google.com/?q=office"
assert f.requests[2][1]["Content-Type"].startswith("multipart/form-data") and JPEG in f.requests[2][2] and f.requests[2][3] == api.MEDIA_TIMEOUT_SECONDS and b'filename="Diwali_offer.jpg"' in f.requests[2][2]
assert "media_items" not in out["metrics"] and "https://maps" not in json.dumps(out["metrics"]), "the stored metrics hold ids and outcomes, not addresses"

f = Fakes("Here you go.\nMEDIA: price_list")
f.answers = [(200, b'{"id": 1}'), (500, b"{}")]
out = api.answer(asked("send prices"), f.deps, NOW)
assert out["metrics"]["media"] == [["price_list", "link"]] and len(f.requests) == 3 and "files.example/" + KEY_PDF in sent_text(f.requests[2]), "a refused attachment becomes a short-lived link"

f = Fakes("Here you go.\nMEDIA: offer")
f.answers = [(200, b'{"id": 1}'), api.TimeoutFailure("timeout")]
out = api.answer(asked(), f.deps, NOW)
assert out["status"] == "replied" and out["metrics"]["media"] == [["offer", "unknown"]] and len(f.requests) == 2, "after a timeout it may have arrived: not sent again"

f = Fakes("Here you go.\nMEDIA: offer", files={KEY_IMG: b"MZ a program renamed"})
out = api.answer(asked(), f.deps, NOW)
assert out["metrics"]["media"] == [["offer", "failed"]] and len(f.requests) == 1, "a file that is not what its name says is never sent"

f = Fakes("Here you go.\nMEDIA: offer", media_items=({**OFFER, "key": KEY_OTHER},))
f.files.put(KEY_OTHER, JPEG)
out = api.answer(asked(), f.deps, NOW)
assert out["status"] == "replied" and out["metrics"]["media"] == [["offer", "failed"]] and len(f.requests) == 1, "another business's file is never read or sent, even if a version names it"

f = Fakes("Here you go.\nMEDIA: offer")
f.deps.files = None
out = api.answer(asked(), f.deps, NOW)
assert out["status"] == "replied" and out["metrics"]["media"] == [["offer", "failed"]] and len(f.requests) == 1, "no file store: the text still arrives"

f = Fakes("We are in Delhi.")
out = api.answer(asked(), f.deps, NOW)
assert out["status"] == "replied" and "media" not in out["metrics"] and len(f.requests) == 1, "no MEDIA line, no media"

f = Fakes("unused")
out = api.answer(asked("", ["image"]), f.deps, NOW)
assert out["status"] == "replied" and out["metrics"]["handover"] == "customer_media" and len(f.requests) == 3, "a photo without words: the sentence, a private note and the status open"
assert json.loads(f.requests[1][2])["private"] is True and "photo, voice note, file or location" in sent_text(f.requests[1]) and f.requests[2][0].endswith("/toggle_status")
assert api.context_of({"attachments": ["image", "Bad!", 5], "channel": "web_widget"})["attachments"] == ["image"] and api.context_of({})["attachments"] == []
assert not any("https://maps" in line or KEY_IMG in line for line in LOGS), "addresses and file names never reach the log"
print("  ok  the worker sends the text first, then each item: attachment, link when refused, nothing twice after a timeout, nothing that is not a real file, and a customer's photo goes to a person")

# ---- a dry run says what it would send
f = Fakes("We are in Delhi.\nMEDIA: offer")
clean = rt.clean_config({**BASE_CONFIG, "media": [OFFER]})
turn = api.dry_turn(clean, "show offer", [], {"channel": "whatsapp", "inbox_id": 3, "attachments": []}, "acme", f.deps, "usage#acme#x", NOW)
assert turn["media"] == [{"id": "offer", "name": "Diwali offer", "kind": "image"}] and turn["text"] == "We are in Delhi." and f.requests == [], "a test sends nothing to Chatwoot"
none = api.dry_turn(clean, "hi", [], {"channel": "whatsapp", "inbox_id": 3, "attachments": []}, "acme", Fakes("Hello there.").deps, "usage#acme#x", NOW)
assert none["media"] == []
check = suite.clean_case({"id": "office", "turns": ["where are you"], "checks": {"media_ids": ["offer"]}}, 1)
assert check["checks"] == {"media_ids": ["offer"]} and suite.evaluate(check, turn, clean) == [] and suite.evaluate(check, none, clean)[0]["check"] == "media_ids"
quiet = suite.clean_case({"id": "no-media", "turns": ["hi"], "checks": {"no_media": True}}, 1)
assert suite.evaluate(quiet, none, clean) == [] and suite.evaluate(quiet, turn, clean)[0]["check"] == "no_media"
for bad in ({"media_ids": []}, {"media_ids": ["Bad Id"]}, {"no_media": False}):
    try:
        suite.clean_case({"id": "x", "turns": ["hi"], "checks": bad}, 1)
        raise AssertionError(bad)
    except suite.Invalid:
        pass
print("  ok  a dry run lists the media it would send and sends none; the suite checks media_ids and no_media")

# ---- Studio: an upload form and a published version
store = api.MemoryStore()
tenant = {"tenant_id": "acme", "enabled": True, "key_hash": "x", "agents": ["shop"], "rev": 1}
store.claim(reg.tenant_key("acme"), tenant)
store.claim(reg.agent_key("shop"), {"tenant_id": "acme", "agent_id": "shop", "account_id": 1, "inbox_ids": [3], "enabled": True, "live_version": 0, "versions": 0, "rev": 1})
files = media.MemoryFiles()
status, form = admin.route(store, tenant, "POST", "/admin/agents/shop/media/upload-url", {"body": json.dumps({"filename": "Offer.JPG", "size": 1000})}, NOW, files=files)
assert status == 200 and form["kind"] == "image" and form["mime"] == "image/jpeg" and form["key"].startswith("chatwoot-media/acme/shop/") and form["key"].endswith(".jpg")
assert form["upload"]["fields"]["key"] == form["key"] and form["upload"]["max_bytes"] == 1000 and form["upload"]["expires_in"] == media.UPLOAD_SECONDS
assert admin.route(store, tenant, "POST", "/admin/agents/ghost/media/upload-url", {"body": "{}"}, NOW, files=files)[0] == 404
assert admin.route(store, {**tenant, "tenant_id": "other"}, "POST", "/admin/agents/shop/media/upload-url", {"body": json.dumps({"filename": "a.jpg", "size": 5})}, NOW, files=files)[0] == 404, "another business's agent is not found"
assert admin.route(store, tenant, "GET", "/admin/agents/shop/media/upload-url", {}, NOW, files=files)[0] == 405
for body in ({"filename": "run.exe", "size": 10}, {"filename": "noext", "size": 10}, {"filename": "a.jpg", "size": media.IMAGE_MAX_BYTES + 1}, {"filename": "a.jpg", "size": 0}, {"filename": "a.jpg"}, {"filename": "a.jpg", "size": 5, "x": 1}):
    try:
        admin.route(store, tenant, "POST", "/admin/agents/shop/media/upload-url", {"body": json.dumps(body)}, NOW, files=files)
        raise AssertionError(body)
    except reg.Invalid:
        pass
files.put(KEY_IMG, JPEG)
status, version = admin.route(store, tenant, "PUT", "/admin/agents/shop/versions/1", {"body": json.dumps({"config": {**BASE_CONFIG, "media": [OFFER, OFFICE]}})}, NOW, files=files)
assert status == 201 and version["version"] == 1
for bad_items in ([{**OFFER, "key": KEY_PDF}], [{**OFFER, "key": "chatwoot-media/acme/shop/" + "f" * 32 + ".jpg"}], [{**OFFER, "key": KEY_OTHER}]):
    try:
        admin.route(store, tenant, "PUT", "/admin/agents/shop/versions/2", {"body": json.dumps({"config": {**BASE_CONFIG, "media": bad_items}})}, NOW, files=files)
        raise AssertionError(bad_items)
    except reg.Invalid:
        pass
print("  ok  Studio gets an upload form only for an allowed type and size of its own agent, and a version cannot be published with a file that is missing, of another type, or another business's")

print("test_chatwoot_media.py: the media library: send photos, videos, files and links, never read them")
