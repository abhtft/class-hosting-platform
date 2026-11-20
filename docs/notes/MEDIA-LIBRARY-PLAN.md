# Media library: the agent can SEND photos, videos, files and links (plan v1, not built)

Status: plan only. Nothing is changed in code. Decisions D1 to D5 below wait for the owner.

## 1. What we want (in the owner's words)

- In the agent's settings there is a card **"Media / attach files"**. The owner uploads a photo, a video, a PDF or adds a link (for example a Google Docs link, a YouTube or Drive video, a Google Maps link).
- Every item has a **name and a short description** written by the owner ("Offer photo for the Diwali batch", "Video: how to reach our office from the bus stand").
- The agent **does not read, analyse or transcribe** the file. It only knows the description. When a customer asks something the description answers ("where are you?", "show me the offer"), the agent **sends that item** together with its normal text answer.
- Customer-sent media (photo, voice note, file) is also **not analysed**: it is received, kept for the person, and the conversation goes to a human (see D3).

Out of scope: vision, OCR, speech to text, video understanding, generating media, media inside the books (RAG) shelf.

## 2. What exists today (checked in code)

| Part | Today |
|---|---|
| Reply to Chatwoot | `chatwoot_core.reply_request(...)`: a JSON POST with `content` only, no attachment. |
| Webhook | `chatwoot_core.classify`: no `attachments` handling. A message with only a file has no text. |
| Agent version config | `chatwoot_runtime.clean_config` (instructions, collections, handover, notes, provider/model). No media. |
| Files | S3 `SitesBucket` already has prefixes; a new prefix is needed. Studio's own backend limit is 1 MB per request, so the upload must go directly to S3 (presigned). |
| Suite | checks route, text, source, length. No media check. |

## 3. Design (small, no new infrastructure family)

**Item** (one DynamoDB item in the Chatwoot table, `media#<tenant>#<agent>#<id>`, plus the file in S3):
`{id, name, kind: image|video|document|link|location, description, when_to_send (optional hint), file: {s3_key, sha256, size, mime} | url, enabled}`.

- **Version-pinned:** the published agent version lists the media ids it may send (`media: [id, ...]`) and the file is stored by content hash, so a published version never changes under it. Editing a file creates a new hash.
- **Files:** Studio asks the platform for a short-lived presigned PUT url (type and size checked), the browser uploads straight to S3. Prefix `chatwoot-media/<tenant>/<agent>/<sha256>`. Not public; the worker reads it with its own role.
- **Links** (Google Docs, YouTube, Drive, Maps) are stored as text. The URL is checked (https only, no private addresses). We do not fetch them.
- **Location** is a text address plus a Google Maps link (sent as a link message, no special Chatwoot location type).

**Choosing what to send (no file reading, only the descriptions):** the prompt gets a short catalogue `[id] name: description` of the version's enabled items (at most 30) and the model returns, besides the answer, `media: ["id1"]` (at most 3, only ids from the catalogue; any other id is dropped). Only when the books or the question justify it; the instruction says "send media only when the description clearly answers the customer; otherwise none".

**Sending:** the worker first posts the text answer as today, then each media as its own Chatwoot message:
- file: the worker reads the object from S3 and posts a multipart `attachments[]` message (Chatwoot's Create New Message accepts multipart attachments; to be re-verified on the Chatwoot version in use);
- link / location: a text message with the link;
- if the multipart post fails: fall back to a short-lived presigned link in the text, and write it in the trace. A failure of media never blocks the text answer and never counts as a handover.

**Customer-sent media:** `classify` recognises a message with attachments and no usable text. The bot answers a fixed, owner-editable line ("Received, a team member will look at it") and hands over with a private note that lists the attachment type (the file itself stays in Chatwoot; we do not copy it).

**Guardrails:** allowed types only (jpg, png, webp, mp4, pdf, docx, xlsx; no executables or archives), size caps from settings (images 5 MB, video 16 MB, documents 20 MB by default, channel-friendly for WhatsApp), at most 30 items per agent, at most 3 media per reply, an item is sent at most once per conversation unless the customer asks again, a media message is never private, and a descriptions is data like any text (it cannot change the rules).

**Cost and trace:** no model cost for the file; storage and transfer only. The answer trace records `media_sent: [ids]` and the delivery result (`attached` / `link` / `failed`).

## 4. Test suite

New check keys for a case: `media_ids` (these ids are sent), `no_media` (none sent). New cases for Margdarshak Digital: office location, offer photo, price list PDF, "no media for an unrelated question", customer sends a photo. The suite counts a failed delivery as a failure.

## 5. Phases (each ends in a checkpoint, each deploy needs its own "Yes, execute")

| Phase | What | Where |
|---|---|---|
| M1 | Media items + presigned upload + config `media` list + prompt catalogue + send (attach, link fallback) + customer-sent media rule + tests + docs + settings | teacher platform (`chatwoot_*`, `template.yaml`, S3 prefix, IAM for the worker only) |
| M2 | "Media" card in the agent editor: upload, add link, description, enable, preview, list in versions | Studio (`AgentPage.tsx`, backend routes) |
| M3 | Suite checks and cases, Test chat shows "would send: ...", nightly drift covers media | both |

IAM change (M1): the worker gets `s3:GetObject` on `chatwoot-media/*` only; the admin function gets `s3:PutObject` presign on the same prefix. Guard tests (`test_template_chatwoot.py`) are updated on purpose.

## 6. Decisions for the owner

- **D1 How the agent picks the media.** A (recommended): the model chooses from the short catalogue of descriptions inside the same call (up to 30 items, no new service). B: search the descriptions with embeddings like the books (better for hundreds of items, more work). Start with A; B later if a business has many items.
- **D2 How a file is delivered.** A (recommended): real attachment in the chat, with a link as automatic fallback. B: always a link (simpler, but the customer must click and the link expires).
- **D3 Media sent by the customer.** A (recommended): fixed reply plus handover with a private note. B: ignore it silently (not good: the customer waits).
- **D4 Whose library.** A (recommended): per agent (versioned with the agent). B: one library per business shared by its agents (less upload repeat, but versions get harder).
- **D5 Location.** A (recommended): address text plus a Google Maps link. B: Chatwoot's native location message (depends on the channel; not all show it).

## 7. Risks and unknowns

1. Chatwoot attachment upload with the **agent bot token** and the exact channel limits (WhatsApp image 5 MB, video 16 MB, document 100 MB, others differ): verify on the live Chatwoot with one test file before M1 is closed.
2. Worker time: reading a 16 MB file and posting it takes seconds; media is sent after the text so the customer already has the answer.
3. Wrong media chosen by the model: limited by the catalogue, the "clearly answers" rule, and suite cases; the trace shows the choice.
4. Copyright and privacy: the owner is responsible for the files; the library is private to the tenant; no public bucket.
5. Storage growth: caps per agent and per tenant in settings; deleting an item deletes the object after the last version using it is retired.
