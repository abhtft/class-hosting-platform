# Agent Studio — Phase 1 FINAL spec (C0 मंज़ूरी के लिए)

**Date:** 2026-11-19
**Status:** मसौदा अंतिम। C0 तभी खुलेगा जब नीचे के **खुले फ़ैसले (O1–O5)** भर जाएँ। कोई code, install, commit या push अभी नहीं।
**मूल स्रोत:** `.temp/HANDOFF-agent-studio.md`, `.temp/PHASE-1-SPEC.md` (executor का draft), और आपके Q-P1 से Q-P7 के जवाब।

---

## 1. तय फ़ैसले (आपके जवाबों से)

| ID | विषय | फ़ैसला | स्रोत |
|---|---|---|---|
| D1 | Purposes | Chatbot: `sales`, `lead_collection`, `support`, `faq`। General: `assistant`, `custom` | Q-P1 = A |
| D2 | Users | सिर्फ़ एक owner, Google sign-in (`class_auth`)। Multi-user बाद में | Q-P2 = A |
| D3 | UI भाषा | सिर्फ़ English UI (labels, messages) | Q-P3 = "only english" (नीचे O7 देखें) |
| D4 | Storage | **हर agent एक record**, जिसमें versions की array है | Q-P4 = B (सीमा के साथ, नीचे O1) |
| D5 | Secrets | portal env variables (platform का तरीका)। Phase 1 में कोई secret नहीं | Q-P5 = A |
| D6 | Starter | Kit रखें। सिर्फ़ demo हटाएँ: `study.py`, `StudyPanel.tsx`, `AgentPanel.tsx`, `patterns.ts`, `evals/study.json`, `app.py` की starter routes | Q-P6 = A |
| D7 | Project files platform पर save | हाँ। **शर्त:** `.temp/`, `.env.local`, `.kit-backup/`, `node_modules/` इसमें कभी न जाएँ (O8) | Q-P7 = हाँ |
| D8 | Git | सीधे main पर काम। हर मंज़ूर checkpoint के बाद push। Force-push कभी नहीं | Handoff §2 |
| D9 | Phase 1 में नहीं | AI call, knowledge upload, Chatwoot, leads, traces, roles, channels, custom domain | Spec §2 |

## 2. खुले फ़ैसले (इनके बिना C0 नहीं खुलेगा)

| ID | सवाल | मेरी सलाह | कौन-सा checkpoint रोकता है |
|---|---|---|---|
| **O1** | **Q-P4 की सीमा।** Record में versions की array होगी, और class storage की value सीमा 64 KB है। Devanagari में 8000 अक्षर UTF-8 में लगभग 24 KB हो सकते हैं। हर version में पूरा instructions कॉपी होता है, इसलिए 2–3 publish में सीमा टूट सकती है। **A)** B में caps: `instructions` ≤ 3000 अक्षर (और ≤ 9 KB UTF-8), versions ≤ 5, record ≤ 56 KB से पहले save रोकें। **B)** Versions अलग records में (Q-P4 = A) **(सलाह)** | B के साथ caps (A) या अलग records (B)। मेरा सुझाव: अलग records, क्योंकि S8 में version हटाना नहीं है और caps उपयोगकर्ता के लिए बंधन बनेंगे | **C4** |
| **O2** | **Q-R (repo ढाँचा):** R1 (अलग repo, kit helpers managed, `KIT-PIN.md` निगरानी-pin) | **R1** Phase 1 के लिए। Phase 3 (Chatwoot) से पहले अलग फ़ैसला (नीचे §6) | **C1** |
| **O3** | **Q-W1 (auto-deploy):** `PORTAL_TOKEN` GitHub secret न डालें; deploy हाथ से `npm run deploy` | **A) नहीं** | **C1** |
| **O4** | **Q-G:** Git remote URL, और repo public है या private | private (सलाह) | **C1** |
| **O5** | **`.temp/` policy:** `.gitignore` में जोड़ें, ताकि business योजना commit न हो | **A) जोड़ें** | **C1** |

## 3. Spec Table (अंतिम; Status अब भरा)

| ID | विषय | अंतिम जवाब | Status |
|---|---|---|---|
| S1 | Repo का रिश्ता | R1 (O2 पर निर्भर) | ⏳ O2 |
| S2 | Users | सिर्फ़ owner (D2) | ✅ मंज़ूर (Q-P2) |
| S3 | UI भाषा | English UI (D3) | ✅ मंज़ूर (Q-P3) |
| S4 | Types | `chatbot`, `general` (allowlist) | ✅ पहले से मंज़ूर (हाँ-मूल्यांकन) |
| S5 | Purposes | D1 | ✅ मंज़ूर (Q-P1) |
| S6 | Fields | `name` ≤80, `description` ≤300, `type`, `purpose`, `language` (`hi`/`en`/`hinglish`), `instructions` ≤ 8000 अक्षर (O1 के बाद सीमा कम हो सकती है) | ⏳ instructions सीमा O1 पर |
| S7 | Draft/Publish | Draft बदलता है; Publish एक immutable version बनाता है; `published_version` pointer | ✅ |
| S8 | Versions | List, read-only देखना, "इस version से draft बनाओ"। हटाना नहीं | ✅ |
| S9 | Delete | Confirm + "recoverable नहीं" लिखा; versions भी हटें | ✅ |
| S10 | Storage | O1 पर निर्भर: (A) `agents` + `agent-versions` अलग records, या (B) caps के साथ एक record | ⏳ O1 |
| S11 | Edit conflict | `updated_at`; पुराने `updated_at` पर save = 409 | ✅ |
| S12 | Auth | `class_auth`; हर route पर `current_user`; दूसरे का agent = 404 | ✅ |
| S13 | Errors | 400/404/409 और storage 503 `{error, message}`; UI `message` दिखाए | ✅ |
| S14 | Layout | Left panel ("New Agent" ऊपर-बाएँ), main में editor/versions, मोबाइल पर सिकुड़े | ✅ |
| S15 | Tests | `evals/studio.json`: CRUD, validation, publish/restore, ownership; हर checkpoint पर `npm run check` | ✅ |
| S16 | Add-ons | `frontend-design`, `responsive`, `accessibility` guides, हर एक पर आपसे पूछकर | ⏳ आपकी हाँ |
| S17 | Project files platform पर save | D7 की शर्त के साथ हाँ | ✅ (O8 की शर्त) |
| S18 | Git शुरुआत | C1 पर `git init -b main`, remote (O4) | ⏳ O4 |
| S19 | Custom domain | बंद; `deploy` वाले address पर | ✅ |

**O7 (D3 की व्याख्या):** मैं "only english" को सिर्फ़ Studio की **UI** के लिए मान रहा हूँ। Customer से बातचीत की भाषा हर agent की `language` field (`hi`/`en`/`hinglish`) से तय होगी, जो Phase 4 में असर डालेगी। अगर आप इसे पूरी तरह English-only चाहते हैं, तो बताएँ।

**O8 (D7 की शर्त):** Project files save में ये शामिल न हों: `.temp/`, `.env.local`, `.class-project.json`, `.kit-backup/`, `node_modules/`। C1 से पहले यह सूची `.gitignore` और save की सूची दोनों में लागू हो।

**S-F (पुरानी `SPEC.md` root पर):** मेरी सिफ़ारिश है कि इसे हटाया जाए, क्योंकि यह पुराने handoff पर आधारित है। यह एक छोटा सवाल है, Phase 1 को नहीं रोकता।

## 4. डेटा मॉडल (O1 पर निर्भर)

**विकल्प A (सलाह):** `agents` collection, record = `{id, owner, name, description, type, purpose, language, instructions, draft_updated_at, published_version}`। `agent-versions` collection, record = `{id: "<agentId>.<n>", agentId, n, published_at, snapshot}`। हर record छोटा रहता है, और versions की संख्या पर कोई ऊपरी सीमा नहीं।

**विकल्प B (आपका चुनाव, caps के साथ):** एक record में `versions: [...]`। सीमाएँ: `instructions` ≤ 3000 अक्षर (≤ 9 KB UTF-8), versions ≤ 5, कुल record ≤ 56 KB। सीमा टूटने पर publish 409 देगा: "इस agent के version की सीमा पूरी हो गई है"। इसमें S8 ("version हटाना नहीं") के साथ टकराव है, इसलिए versions की सीमा पर आपको कोई रास्ता चुनना होगा।

## 5. Hard constraints (Boundary)

1. Layout: `frontend/` और `backend/`; नाम नहीं बदलेंगे।
2. Backend सिर्फ़ Python 3.12 stdlib। कोई pip package नहीं। एक function।
3. Page सिर्फ़ `fetch('/api/...')` से बात करे (`frontend/src/api.ts`); कोई localhost या पूरा URL नहीं।
4. Browser में कोई secret नहीं; `VITE_` में secret नहीं।
5. Managed फ़ाइलें नहीं छुएँगे: `class_*.py`, `tools/`, `portal-deploy.mjs`, `platform.json`, `kit-manifest.json`, `.github/workflows/deploy.yml`, kit docs।
6. अपना login, password, JWT या token-code नहीं। सिर्फ़ `class_auth`।
7. `.env.local` कभी नहीं खोला, पढ़ा या print किया जाएगा। Token सिर्फ़ आप `npm run login` से देंगे। चैट में असली key आए तो उसे इस्तेमाल नहीं करेंगे और revoke करने को कहेंगे।
8. Phase 1 में नहीं छूना: `class_ai`, `class_knowledge`, `class_agent`, jobs, Chatwoot।
9. Auto-deploy नहीं (O3)। Push से deploy नहीं होगा।
10. `check` की कोई ✖ चुप या बाइपास नहीं होगी।
11. कोई install, build, commit, push, deploy या Google sign-in चालू करना आपकी स्पष्ट हाँ के बिना नहीं।
12. Instructions में secret या निजी डेटा न रखने की चेतावनी UI में।

## 6. Checkpoints

| # | Checkpoint | Gate |
|---|---|---|
| **C0** | यह spec मंज़ूर; O1–O5 भरे | आपकी लिखित हाँ |
| **C1** | `.gitignore` में `.temp/` (O5); `git init -b main`, remote (O4), `KIT-PIN.md` (O2); पहला commit सिर्फ़ मौजूदा starter + spec; project files save (D7/O8) | `git status` साफ़; आपकी हाँ; push |
| **C2** | Starter की सफ़ाई (D6), खाली shell, `npm run check` | `check` साफ़; `npm run dev` में खाली shell |
| **C3** | Sign-in: local "Local Visitor"; platform पर `signin -- on` (आपकी हाँ के बाद) | `/auth/me` जवाब; बिना sign-in 401 |
| **C4** | Backend CRUD + validation + ownership + 409 (O1 के बाद storage) | `npm run eval` पास |
| **C5** | Publish / versions / restore | eval पास |
| **C6** | Frontend: left panel, "New Agent" wizard, list | `npm run dev` में दिखाना |
| **C7** | Editor, Save, versions UI | demo + आपकी राय |
| **C8** | Phase 1 gate: agent बने, publish हो, version पढ़ा जाए; `check` + `eval` पास; deploy (स्पष्ट हाँ के बाद) | `/api/health` |

हर checkpoint पर: क्या बदला, कैसे आज़माएँ, सीमाएँ, और अगला कदम।

## 7. Phase 3 के लिए एक चेतावनी (अभी फ़ैसला नहीं)

Studio अभी student kit पर है। उस पर Secrets Manager नहीं, फ़ाइल सीमा 5 MB, background job सिर्फ़ page से शुरू होता है, और gateway की origin-secret जाँच Chatwoot webhook को रोक सकती है। Phase 3 से पहले तय करना होगा: (R3) अलग AWS stack, या (R-T) runtime teacher platform पर। यह Phase 1 को नहीं रोकता।

## 8. Executor के लिए अगला कदम (C0 के बाद)

1. O1–O5 के जवाब लें।
2. C0 पर `.temp/PHASE-1-SPEC.md` (executor की मूल फ़ाइल) को इस FINAL से मिलाएँ। मूल फ़ाइल न बदलें, अंतिम बदलाव इसी FINAL में लिखें।
3. C1 तक कोई build या push नहीं।
