"""When the bot hands a conversation to a person (pure rules: no AWS, no network).

Chatwoot's own mechanism is used: a conversation the bot serves is `pending`; setting it to `open` puts it in the team's list and notifies. The worker (chatwoot_api.py)
does three things at a hand-over, each on its own so one failing does not stop the others: it sends the customer ONE fixed sentence (the agent's `handover_text`), it
posts a PRIVATE note for the team (why, and the last messages), and it sets the conversation to `open`.

The reasons (the first that applies, in this order):
  person_asked  the customer asks for a person ("call me", "baat karni hai", "insaan", ...)
  complaint     a complaint or an angry or money-trouble word ("fraud", "dhokha", "refund chahiye", ...); a plain question about the refund policy is NOT one
  warm_lead     the customer shares a mobile number, or says they want to go ahead or visit ("book karna", "aana hai", ...): speed matters, the team is told at once
  stuck         the books were not sure on two messages in a row (decided by the worker from the conversation memory)
  customer_media  the customer sent a photo, voice note, file or place with no words: the bot does not read these, so a person looks at them
Failures of the answer (the model, the search, the spend cap) and stuck message items found by the sweeper hand over too (chatwoot_api.py / chatwoot_sweeper.py).
A hand-over never repeats: when the last remembered turn was a hand-over the rules do not fire again.
The word lists are short on purpose and are a first version: they are read from the customer's own words in the event and nothing here stores them.
"""
import re

import platform_settings

NOTE_MAX_CHARS = platform_settings.value("chatwoot.noteMaxChars")

TRIGGERS = ("person_asked", "complaint", "warm_lead", "stuck", "customer_media")
FAILURE_REASONS = ("llm_error", "retrieval_error", "no_shelf", "cap_reached", "bad_config", "agent_error", "department_unavailable")  # an answer that could not be made: the customer must not be left in silence
DEFAULT_TEXT = "Dhanyavaad! Aapki jankari hamari team ko mil gayi hai. Team jaldi aapse sampark karegi (Mon-Sat, 9 AM se 6 PM). Tab tak aap yahin likh sakte hain."

_PHONE = re.compile(r"(?<!\d)(?:(?:\+|00)?91[ -]?)?[6-9](?:[ -]?\d){9}(?!\d)")  # an Indian mobile number, with or without +91 and spaces

_PERSON = re.compile(
    r"\b(?:talk to (?:a |an |the )?(?:person|human|someone|agent|team|staff|executive)|speak (?:to|with) (?:a |an |the )?(?:person|human|someone|agent|team|staff|executive)"
    r"|real person|human agent|live agent|customer care|call me|call back|callback|contact me|phone me|baat karni|baat karna|baat kar(?:wa|va)|baat kijiye|kisi se baat"
    r"|insaan|insan|aadmi se|team se baat|agent se|person se|human se|call karo|call kijiye|phone karo|phone kijiye)\b")
_PERSON_DEVANAGARI = ("इंसान", "इन्सान", "बात करनी", "बात करना", "बात करवा", "कॉल करो", "कॉल कीजिए", "फोन करो", "फ़ोन करो", "किसी से बात", "एजेंट", "टीम से बात")

_COMPLAINT = re.compile(
    r"\b(?:cheat(?:ed|ing)?|fraud|scam|dhokha|dhoka|complaint|shikayat|refund (?:chahiye|nahi|nhi)|(?:want|need) (?:my )?(?:refund|money back)|money back|paisa wapas|paise wapas"
    r"|consumer court|legal action|police|worst service|bad service|not happy|very bad|bekar service)\b")
_COMPLAINT_DEVANAGARI = ("शिकायत", "धोखा", "धोखाधड़ी", "पैसे वापस", "पैसा वापस", "रिफंड चाहिए", "घटिया")

_WARM = re.compile(
    r"\b(?:want to (?:start|book|join|buy|take|enrol|enroll|visit)|ready to (?:start|join|buy)|book karna|book kar|booking|enrol(?:l|lment)?|admission|aana hai|aana chahta|aana chahti"
    r"|milna hai|visit karna|shuru karna|start karna|le lena|le lunga|le lungi|join karna|demo chahiye|meeting)\b")
_WARM_DEVANAGARI = ("आना है", "मिलना है", "शुरू करना", "बुक करना", "एडमिशन", "जॉइन करना", "डेमो चाहिए")

LABELS = {
    "person_asked": "the customer asked for a person",
    "complaint": "the customer wrote a complaint or an angry message",
    "warm_lead": "the customer shared a mobile number or wants to go ahead: a warm lead",
    "stuck": "the bot could not answer two messages in a row",
    "customer_media": "the customer sent a photo, voice note, file or location without any words (the bot does not read these): please open the conversation",
    "stuck_item": "the bot's reply to the last message may not have been delivered (the worker did not finish): please read this conversation",
    "department_unavailable": "the department that should answer is switched off, has no live version or is not set up: please answer this customer",
}


def _has(text, pattern, words):
    return bool(pattern.search(text)) or any(word in text for word in words)


def decide(text, history, on, attachments=()):
    """The reason to hand over for this message, or None. `on` is the agent's list of enabled reasons; `history` the remembered turns (a hand-over is not repeated); `attachments`
    the kinds of file the message carries (words only: the file itself is never read)."""
    if history and history[-1].get("h") == 1:
        return None
    lowered = str(text or "").lower()
    if not lowered.strip():
        return "customer_media" if attachments and "customer_media" in on else None
    if "person_asked" in on and _has(lowered, _PERSON, _PERSON_DEVANAGARI):
        return "person_asked"
    if "complaint" in on and _has(lowered, _COMPLAINT, _COMPLAINT_DEVANAGARI):
        return "complaint"
    if "warm_lead" in on and (_PHONE.search(str(text)) or _has(lowered, _WARM, _WARM_DEVANAGARI)):
        return "warm_lead"
    return None


def label(reason):
    """The team's words for a reason: a trigger, a stuck item, or the code of a failed answer."""
    return LABELS.get(reason) or f"the bot could not answer ({reason})"


def note_text(reason, history, text):
    """The private note for the team: why, and the last messages (the memory's turns and the message that caused it). Cut to the setting."""
    lines = ["Bot hand-over: " + label(reason) + "."]
    if history or text:
        lines.append("Last messages:")
    for turn in history:
        lines += [f"Customer: {turn['c']}", f"Bot: {turn['b']}"]
    if text:
        lines.append(f"Customer: {text}")
    return "\n".join(lines)[:NOTE_MAX_CHARS]
