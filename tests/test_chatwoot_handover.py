"""The hand-over rules (lambda/chatwoot_handover.py) and the two Chatwoot calls that carry a hand-over (lambda/chatwoot_core.py): which words ask for a person, make a complaint or
show a warm lead, what an ordinary question is NOT, that a hand-over is not repeated, that each reason can be switched off, the private note, and the exact requests for a private note
and for the status `open`."""
import json
import os
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_core as core  # noqa: E402
import chatwoot_handover as ho  # noqa: E402

ALL = list(ho.TRIGGERS)

# a person asked for
for text in ("Can I talk to a person?", "I want to speak with a human", "please call me", "CALL BACK later", "real person please", "mujhe insaan se baat karni hai", "kisi se baat karwa dijiye",
             "team se baat karni hai", "customer care number batao call kijiye", "मुझे इंसान से बात करनी है", "कॉल करो", "एजेंट चाहिए"):
    assert ho.decide(text, [], ALL) == "person_asked", text
# a complaint
for text in ("this is a fraud", "You cheated me", "I want my money back", "mujhe refund chahiye", "dhokha hai ye", "paisa wapas karo", "I will go to consumer court", "worst service ever", "मुझे शिकायत करनी है", "आपने धोखा दिया"):
    assert ho.decide(text, [], ALL) == "complaint", text
# a warm lead: a mobile number (any usual way of writing it) or a wish to go ahead
for text in ("9876543210", "my number is 98765 43210", "+91 98765-43210", "call 919876543210", "0091 9876543210", "I want to book a classroom", "I want to start next month", "I am ready to join", "booking karna hai",
             "kal aana hai", "milna hai", "demo chahiye", "मुझे कल आना है", "एडमिशन लेना है", "मुझे शुरू करना है"):
    assert ho.decide(text, [], ALL) == "warm_lead", text
# NOT a reason: ordinary questions, a refund POLICY question, an interest, other numbers, empty
for text in ("What services do you offer?", "what is the refund policy", "Refund milta hai kya?", "interested in social media management", "price of content creation please", "do you sell books",
             "order 123456789012 status", "my pin is 846003", "5000 rupees", "hello", "hi", "", "   ", None, "I called the number yesterday", "Aap kaun si services dete ho?"):
    assert ho.decide(text, [], ALL) is None, repr(text)
print("  ok  the word lists find a person asked for, a complaint and a warm lead in English, Hinglish and Hindi, and leave ordinary questions, interest and other numbers alone")

# the order of the reasons: the person first, then the complaint, then the lead
assert ho.decide("this is a fraud, call me on 9876543210", [], ALL) == "person_asked"
assert ho.decide("fraud! my number is 9876543210", [], ALL) == "complaint"
# each reason can be switched off
assert ho.decide("call me", [], ["complaint", "warm_lead"]) is None and ho.decide("call me", [], []) is None
assert ho.decide("fraud", [], ["person_asked"]) is None and ho.decide("9876543210", [], ["person_asked", "complaint"]) is None
assert ho.decide("9876543210", [], ["warm_lead"]) == "warm_lead"
# a hand-over is not repeated at once
assert ho.decide("call me", [{"c": "x", "b": "y", "h": 1}], ALL) is None and ho.decide("call me", [{"c": "x", "b": "y"}, {"c": "x", "b": "y", "f": 1}], ALL) == "person_asked"
print("  ok  the first reason that applies wins, every reason can be switched off, and a conversation just handed over is not handed over again")

# the private note: why, and the last messages, cut to the setting
note = ho.note_text("warm_lead", [{"c": "hello", "b": "Namaste"}, {"c": "price?", "b": "5,000 rupees", "f": 1}], "my number is 9876543210")
assert note == "Bot hand-over: the customer shared a mobile number or wants to go ahead: a warm lead.\nLast messages:\nCustomer: hello\nBot: Namaste\nCustomer: price?\nBot: 5,000 rupees\nCustomer: my number is 9876543210"
assert ho.note_text("llm_error", [], "") == "Bot hand-over: the bot could not answer (llm_error)."
assert "may not have been delivered" in ho.note_text("stuck_item", [], "") and "stuck" not in ho.label("person_asked")
assert len(ho.note_text("stuck", [{"c": "x" * 400, "b": "y" * 400}] * 6, "z" * 400)) == ho.NOTE_MAX_CHARS == 1500
assert set(ho.TRIGGERS) == {"person_asked", "complaint", "warm_lead", "stuck"} and "cap_reached" in ho.FAILURE_REASONS and "no_live_version" not in ho.FAILURE_REASONS, "no live version is silence on purpose"
assert 0 < len(ho.DEFAULT_TEXT) <= 500
print("  ok  the private note says why and shows the last messages, is cut to the setting, and an agent with no live version stays silent on purpose")

# the calls to Chatwoot: a private note is a reply with private true; the status is toggle_status with its own body; the same address, header and checks
url, headers, body = core.reply_request("https://chat.example.org", "bearer", "abcDEF123456xyz789", 1, 12, "why", private=True)
assert url == "https://chat.example.org/api/v1/accounts/1/conversations/12/messages" and json.loads(body) == {"content": "why", "message_type": "outgoing", "private": True}
assert json.loads(core.reply_request("https://chat.example.org", "bearer", "abcDEF123456xyz789", 1, 12, "hi")[2])["private"] is False, "a normal reply is public"
url, headers, body = core.status_request("https://chat.example.org", "api_access_token", "abcDEF123456xyz789", "1", 12, "open")
assert url == "https://chat.example.org/api/v1/accounts/1/conversations/12/toggle_status" and json.loads(body) == {"status": "open"}
assert headers["api_access_token"] == "abcDEF123456xyz789" and "Authorization" not in headers and headers["User-Agent"] == core.USER_AGENT
for odd in ("1/../2", "x", None):
    try:
        core.status_request("https://chat.example.org", "bearer", "abcDEF123456xyz789", odd, 1, "open")
        raise AssertionError(odd)
    except (ValueError, TypeError):
        pass
assert core.status_result(200, b'{"payload": {"success": true}}') == ("ok", None) and core.status_result(200, b"") == ("ok", None)
assert core.status_result(403, b"{}") == ("failed", "http_403") and core.status_result(404, b"") == ("failed", "http_404") and core.status_result(502, b"") == ("failed", "http_5xx") and core.status_result(302, b"") == ("failed", "http_other")
print("  ok  a private note is a reply with private true, the status change is toggle_status with {status: open}, both with the same checks, and its answer is read as ok or a short code")

print("test_chatwoot_handover.py: hand-over rules, note and Chatwoot calls")
