# Agent Studio — Phase 1 spec (Studio shell)

**Status:** DRAFT, मंज़ूरी का इंतज़ार। कोई code, install, commit या push नहीं हुआ।
**Date:** 2026-11-19
**आधार:** `.temp/HANDOFF-agent-studio.md` (पूरा), `AGENTS.md`, `platform.json`, `kit-manifest.json`, `README.md`, `PATTERNS.md`, `INTERVIEW.md`, `.gitignore`, `backend/app.py`, `frontend/src/*`, और live `node portal-deploy.mjs status`।
**बदले नहीं गए फ़ैसले (handoff §2 और §7):** Q1=A, Q2=A, Q3=B, Q4=तीसरा विकल्प (AI-रहित media + description), Q5=A, और main पर सीधा काम + हर मंज़ूर checkpoint के बाद push।

---

## 0. पहले यह: Q1 का विरोधाभास (साफ़ शब्दों में)

Handoff में दो बातें टकराती हैं:

- §2: Studio एक **अलग repo/project** है, teacher-platform repo के अंदर नहीं।
- §7 Q1=A: runtime एक **"agent" module AWS kit के अंदर** हो (code reuse, एक install प्रति organisation)।

Handoff खुद कहता है कि इसे Phase 1 spec में सुलझाओ और coding से पहले आपसे पूछो। यहाँ जाँच से तीन तथ्य निकले जो इसे और तीखा बनाते हैं:

1. **यह folder पहले से एक class-platform student project है** (token "studio", portal `deploy.margdarshakss.com`)। इसमें teacher-repo का core (RAG hybrid search, embeddings, gateway, jobs engine) **है ही नहीं**। यहाँ सिर्फ़ पतले client helpers हैं: `class_ai`, `class_storage`, `class_knowledge`, `class_agent`, `class_quality`, `class_auth`। असली RAG/embeddings platform की तरफ़ सेवा के रूप में चलते हैं।
2. **"Pinned copy" इन helpers पर लागू नहीं होती।** ये सब `platform.json` में `managed` हैं: हर `deploy` और `status` पर kit इन्हें teacher के portal से मिलाकर **अपने-आप बदल देता है** (पुरानी copy `.kit-backup/` में)। हम इन्हें edit या freeze नहीं कर सकते।
3. **AWS Secrets Manager और अलग AWS account इस platform पर विकल्प नहीं हैं।** Backend सिर्फ़ stdlib, एक function, secrets सिर्फ़ portal के Environment variables में (AGENTS.md rule 7)।

### प्रस्तावित समाधान (आपकी पुष्टि चाहिए, Q1 का फ़ैसला नहीं बदलता)

**R1 (मेरी सलाह):** Studio = **अलग repo** (यही `studio/`, teacher repo से अलग)। "Kit core की pinned copy" का मतलब:
- **Pin का रिकॉर्ड:** एक फ़ाइल `KIT-PIN.md` में kit version (अभी 2.11.0, serial 1795091023) और `kit-manifest.json` का hash दर्ज होगा। हर kit update पर हम `npm run check` + `npm run eval` चलाकर देखेंगे कि Studio टूटा तो नहीं। असली freeze नहीं, **निगरानी वाला pin**।
- **Studio का अपना code** `backend/studio/` (नया, managed सूची में नहीं) और `frontend/` में रहेगा। "agent runtime module" (Phase 3 से) यही package होगा, एक ही function के अंदर, और managed helpers को सिर्फ़ import करेगा।
- **Core की जो चीज़ें kit में नहीं हैं** (hybrid search आदि): हम उन्हें `class_knowledge` सेवा से उसके दायरे में ही इस्तेमाल करेंगे; अपनी copy नहीं बनाएँगे।

**R2:** managed helpers की सच में frozen copy अलग नाम से रखना। असली pin, पर kit के सुधार/सुरक्षा fixes नहीं मिलेंगे, और `check` "अपना AI client मत लिखो" नियम पर आपत्ति कर सकता है।
**R3:** पूरा अलग AWS stack/account (handoff §2 की "separate stack" सिफ़ारिश)। तब यह class platform का app नहीं रहेगा; teacher/AWS access और बहुत ज़्यादा काम चाहिए।

> **Q-R. आप R1, R2 या R3 चुनें?** (Phase 1 का बाकी spec R1 मानकर लिखा है।)

---

## 1. Starter kit audit

`platform.json` की `managed` सूची और `kit-manifest.json` (83 फ़ाइलें) से मिलाकर। **नतीजा: kit में कोई "teacher-only" फ़ाइल नहीं है** (सब student-facing है)। जो हटाया जा सकता है वह सिर्फ़ **student-owned demo code** है। Managed फ़ाइलें हटाना बेकार है: अगली sync उन्हें लौटा देगी, और `check` टूट सकता है।

### A. रखो और छुओ मत (managed, kit sync इन्हें सँभालता है)
| फ़ाइलें | क्यों |
|---|---|
| `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.windsurfrules`, `.github/copilot-instructions.md`, `.claude/`, `.cursor/` | AI-editor के नियम; platform rule 10 |
| `tools/` (check, dev, dev_server, eval, knowledge, platform, project, signing, zip, addons...), `portal-deploy.mjs`, `platform.json`, `kit-manifest.json` | deploy/check/eval की मशीनरी |
| `.github/workflows/deploy.yml`, `.vscode/mcp.json` | deploy (नीचे चेतावनी W1) |
| `backend/class_ai.py`, `class_storage.py`, `class_knowledge.py`, `class_agent.py`, `class_quality.py`, `class_auth.py` | Studio इन्हीं पर बनेगा |
| `variants/node-backend/*` | managed; हम Python रखेंगे, पर इसे हटाना sync से टकराएगा, इसलिए पड़ा रहने दो |
| `PATTERNS.md`, `INTERVIEW.md`, `PROMPTS.md` | संदर्भ; `PROMPTS.md` Studio के काम का नहीं पर managed है |

### B. Studio के लिए बदलो या हटाओ (student-owned demo, हमारी अपनी फ़ाइलें)
| फ़ाइल | कार्रवाई |
|---|---|
| `frontend/src/App.tsx` (starter page) | **बदलो**: Studio shell |
| `frontend/src/StudyPanel.tsx`, `AgentPanel.tsx` | **हटाओ** (AgentPanel का approval-card पैटर्न सिर्फ़ संदर्भ; live chat में human approval नहीं, handoff §5) |
| `frontend/src/patterns.ts` | **हटाओ** (study/agent के types) |
| `backend/study.py` | **हटाओ** (+ `app.py` से `import study` और `study.route(...)`) |
| `evals/study.json` | **हटाओ**; Studio के अपने cases बनाओ |
| `backend/app.py` की starter routes: `/hello`, `/echo`, `/ask`, `/notes`, `/private` | **हटाओ**; `/health` और `class_auth.handle` रखो; `run_job` ढाँचा रखो (Phase 3 के लिए) |
| `README.md`, `frontend/src/styles.css`, `frontend/index.html` | **दोबारा लिखो** (Studio के नाम/look के लिए) |
| `.env.example` | रखो; ज़रूरत पड़ने पर commented लाइनें जोड़ो |

### C. Student-owned और अपने आप बनी: मत छुओ / पढ़ो मत
`.env.local` (token; **कभी नहीं खोलूँगा**), `.platform-notice.json`, `.kit-backup/` (git-ignored), `frontend/node_modules/`, `.gitignore` (सही है: `.env*`, `*.local`, `/knowledge/` बाहर)।

### D. नई फ़ाइलें (Phase 1 में प्रस्तावित)
`KIT-PIN.md` (R1 पर), `SPEC.md` या इसी `.temp/` spec का स्थायी रूप (आपकी पसंद), `backend/studio/` (CRUD logic), `frontend/src/` में shell components, `evals/studio.json`।

### E. Add-ons (सिर्फ़ पूछकर, AGENTS.md के अनुसार)
Phase 1 UI के काम के: `frontend-design`, `responsive`, `accessibility`, `forms` (सिर्फ़ markdown guides; `forms` के साथ कोई package?). Package वाले (recharts, icons) अभी नहीं। हर एक आपसे "Add करूँ?" पूछकर।

### F. पुरानी `SPEC.md` (project root)
मैंने पिछले चरण में root पर `SPEC.md` लिखा था। वह पुराने handoff पर आधारित था और अब इन फ़ैसलों से टकराता है। मैं उसे इस spec की मंज़ूरी के बाद हटा दूँगा (या अभी हटाऊँ, आप बताएँ)। वह मेरी अपनी बनाई फ़ाइल है।

---

## 2. Intent Capture checklist

1. **Product:** Chatwoot के Agent Bot के रूप में जुड़ने वाला अपना agent platform; Anthropic-console जैसा look। *(handoff §1)*
2. **Phase 1 का एकमात्र लक्ष्य:** Studio shell। Left panel, "New Agent" (ऊपर-बाएँ), wizard (Type: Chatbot/General; Purpose), agent CRUD, versioning। **AI, knowledge, Chatwoot कुछ नहीं।**
3. **Gate (handoff §6):** एक agent बन सके, publish हो सके, और उसका version पढ़ा जा सके।
4. **कौन इस्तेमाल करेगा:** सिर्फ़ आप (एक developer/owner)? बाद में कई लोग (Phase 7 roles)?
5. **Studio की भाषा:** UI और बातचीत की भाषा क्या हो?
6. **Purposes की सूची:** Sales bot, Lead collection bot, और क्या?
7. **"Published version" का मतलब:** runtime (Phase 3+) सिर्फ़ published version पढ़ेगा; draft कभी live नहीं होता।
8. **Phase 1 में क्या नहीं:** AI call, knowledge upload, Chatwoot token/webhook, leads, traces, roles, channels, custom domain।
9. **Platform सीमाएँ जो असर डालती हैं:** class storage (SQL नहीं, value ≤ 64 KB, पेज 100, search/sort नहीं, दो writer में आख़िरी जीतता है), 27 s request, custom domain बंद, Python stdlib।
10. **जोखिम जो अभी verify नहीं:** Google sign-in platform पर चालू है या नहीं; Chatwoot webhook से background job शुरू हो सकती है या नहीं (job सिर्फ़ page के `/api/_jobs/<name>` से लिखी है)।
11. **Git:** main पर सीधा काम, हर मंज़ूर checkpoint के बाद push, force-push कभी नहीं। *(handoff §2/§8)*

---

## 3. Spec Table (मेरे प्रस्तावित जवाब; Status आप भरेंगे)

| ID | विषय | मेरा प्रस्तावित जवाब | Status |
|---|---|---|---|
| S1 | Q1 का समाधान | **R1**: अलग repo, kit helpers (managed, auto-update) + `KIT-PIN.md` निगरानी-pin + अपना `backend/studio/`; runtime = इसी function का हिस्सा | |
| S2 | Users | सिर्फ़ एक owner (Google sign-in). Multi-user / roles Phase 7 | |
| S3 | UI भाषा | English UI (labels), बातचीत Hindi में; i18n toggle बाद में | |
| S4 | Types | `chatbot`, `general` (allowlist) | |
| S5 | Purposes | chatbot: `sales`, `lead_collection`, `support`, `faq`; general: `assistant`, `custom` | |
| S6 | Agent के fields | `name` (≤80), `description` (≤300), `type`, `purpose`, `language` (`hi`/`en`/`hinglish`), `instructions` (≤8000 अक्षर, सिर्फ़ store होगा, Phase 1 में कोई model नहीं) | |
| S7 | Draft/Publish | Agent record में draft; Publish एक **immutable version** बनाता है (`v1, v2...`) और `published_version` pointer सेट करता है | |
| S8 | Versions | list, read-only देखना, "इस version से draft बनाओ" (restore). Version हटाना नहीं | |
| S9 | Delete | Agent delete पर confirm; versions भी हटेंगे; "recoverable नहीं" साफ़ लिखा होगा | |
| S10 | Storage | class storage: collection `agents` (id=`<owner>-<ms>-<rand>`), `agent-versions` (id=`<agentId>.<n,4अंक>`); value ≪ 64 KB | |
| S11 | Edit conflict | Save बटन + unsaved चेतावनी; record में `updated_at`; पुराने `updated_at` पर save तो 409 (आख़िरी-जीतता-है से बचाव, सिर्फ़ एक owner में भी दो tab के लिए) | |
| S12 | Auth | `class_auth` (Google). हर route पर `current_user`; owner page से नहीं लिया जाएगा; दूसरे का agent = 404 | |
| S13 | Errors | 400/404/409 `{error,message}`; storage त्रुटि 503 `{error,message}`; page हर `error.message` दिखाए | |
| S14 | Layout | Left panel (ऊपर "New Agent", नीचे agents की सूची), main में editor/versions; मोबाइल पर panel सिकुड़े | |
| S15 | Tests | `evals/studio.json`: CRUD, validation, publish/restore, "दूसरे का agent नहीं दिखता"; `npm run check` हर checkpoint पर | |
| S16 | Add-ons | `frontend-design`, `responsive`, `accessibility` guides (हर एक पर आपसे पूछकर) | |
| S17 | Project files platform पर save | AGENTS.md कहता है एक बार पूछो: "हाँ/नहीं" | |
| S18 | Git शुरुआत | अभी यह folder git repo नहीं है। C0 पर आप remote URL दें; मैं `git init -b main` और remote जोड़ूँगा (आपकी हाँ के बाद) | |
| S19 | Custom domain | अभी बंद (teacher का switch); Phase 1 `deploy` वाले address पर | |

---

## 4. Boundary Document

### Hard constraints
1. **Layout fixed:** `frontend/` + `backend/`; नाम नहीं बदलेंगे।
2. Backend सिर्फ़ **Python 3.12 stdlib**; कोई pip package नहीं; एक function, कोई port/server नहीं।
3. Page सिर्फ़ `fetch('/api/...')` (`frontend/src/api.ts`) से बात करे; localhost या पूरा URL कभी नहीं।
4. **Browser में कोई secret नहीं।** `VITE_` में secret नहीं। AI को browser से नहीं बुलाएँगे (वैसे Phase 1 में AI है ही नहीं)।
5. **Managed फ़ाइलें edit नहीं** (class_*.py, tools/, portal-deploy.mjs, platform.json, deploy.yml, kit docs)। `kit-manifest.json` भी नहीं।
6. **अपना login/password/JWT/token-code नहीं।** सिर्फ़ `class_auth`. `CLASS_AUTH_*` कहीं नहीं लिखना।
7. **`.env.local` कभी नहीं खोलूँगा/पढ़ूँगा/print करूँगा।** Token सिर्फ़ `npm run login` (आप खुद) से; आप मुझे token कभी न दें। चैट में कोई असली key आए तो मैं उसे इस्तेमाल नहीं करूँगा और revoke करने को कहूँगा।
8. Secrets (बाद में Chatwoot token आदि): सिर्फ़ portal env variables; किसी फ़ाइल में नहीं; Phase 1 में कोई secret है ही नहीं।
9. Model को कभी कोई secret नहीं मिलेगा (बाद के phases के लिए लागू रहने वाला नियम)।
10. Storage allowance बचाओ: loop में calls नहीं; list एक बार पढ़ो; records छोटे; instructions में secret/निजी डेटा न रखने की चेतावनी UI में।
11. हर नया route validation के साथ; `check` की किसी `✖` को चुप नहीं कराएँगे, न बाइपास करेंगे।
12. **Phase 1 में नहीं छूना:** `class_ai`, `class_knowledge`, `class_agent`, jobs, Chatwoot।
13. **कोई install नहीं:** `npm install`/नए packages आपकी स्पष्ट माँग के बिना नहीं (add-on सिर्फ़ पूछकर)।

### Git और deploy चेतावनियाँ
- **W1 (मुख्य):** main पर push से GitHub Action **अपने-आप deploy** कर सकता है, **अगर** repo पर `PORTAL_TOKEN` secret सेट है। हर checkpoint-push तब एक live deploy भी होगा। या तो (a) repo पर secret न डालो और deploy `npm run deploy` से अलग से चलाओ, या (b) जानबूझकर auto-deploy रखो। **Q-W1: कौन-सा?**
- हर push से पहले: `npm run check` साफ़, और आपकी checkpoint-मंज़ूरी। Force-push कभी नहीं।
- Commit संदेशों के अंत में attribution लाइन।
- `.env.local`, `.class-project.json`, `.kit-backup/` commit में नहीं जाएँगे (`.gitignore` में हैं)।

### जो मैं आपसे पूछे बिना नहीं करूँगा
कोई build/install/commit/push/deploy; add-on जोड़ना; Google sign-in चालू करना (`npm run signin -- on`); फ़ाइल हटाना; platform पर project files push करना।

### ज्ञात सीमाएँ (आपको बतानी हैं)
27 s request; storage में search/sort नहीं और दो लोगों के एक साथ edit में आख़िरी जीतता है (S11 इसे घटाता है); सब limits per-student हैं, per-agent/organisation नहीं; custom domain बंद; Hindi/Hinglish retrieval English-only stop-words पर टिकी है (Phase 4 का मुद्दा, handoff §4)।

### Phase 2+ पर इस audit का असर (अभी सिर्फ़ चेतावनी, Phase 1 को नहीं रोकता)
- **Q3=B (Google OAuth + Drive, Secrets Manager):** इस platform पर Secrets Manager नहीं; OAuth refresh tokens रखने की जगह (env var या class storage) एक नया फ़ैसला है, और per-user token env variable में नहीं रखे जा सकते। Phase 2 spec में अलग से सुलझाएँगे।
- **DOCX:** `class_knowledge` सिर्फ़ PDF/text/MD/HTML लेता है; DOCX को stdlib (`zipfile` + XML) से text बनाकर जोड़ना होगा।
- **Per-agent shelves:** teacher collections की संख्या per-student सीमित करता है; कई agents तक फैलने पर यह सीमा आएगी।
- **Q4 media:** फ़ाइल सीमा 5 MB (class_storage); बड़े videos के लिए जगह/तरीका अलग से देखना होगा।
- **Chatwoot webhook (Phase 3):** HMAC सत्यापन stdlib `hmac` से हो सकता है; platform gateway की origin-secret जाँच कैसे बाइपास/अनुकूल होगी और webhook से job शुरू होगी या नहीं, यह teacher से पूछना होगा। Create-Message endpoint और payload fields अभी verify नहीं (handoff §10)।

---

## 5. Checkpoints (हर एक पर रुकना; मंज़ूरी के बाद ही अगला; मंज़ूरी के बाद ही push)

| # | Checkpoint | Gate / सबूत |
|---|---|---|
| **C0** | यह spec मंज़ूर: Q-R (R1/R2/R3), Q-W1, Spec Table का Status, Git remote | आपकी लिखित हाँ |
| **C1** | Git: `git init -b main`, remote, पहला commit (सिर्फ़ मौजूदा starter + spec, कोई बदलाव नहीं) और `KIT-PIN.md`; Project files save का सवाल (S17) | `git status` साफ़; आपकी हाँ; push |
| **C2** | Starter की सफ़ाई (B सूची), खाली Studio shell, `npm run check` साफ़ | `check` साफ़; `npm run dev` में खाली shell |
| **C3** | Sign-in: local में "Local Visitor"; (आपसे पूछकर) platform पर `signin -- on` | `/auth/me` जवाब; बिना sign-in 401 |
| **C4** | Backend: agents CRUD + validation + ownership + 409 | `npm run eval` पास; curl से दिखाऊँगा |
| **C5** | Backend: publish / versions / restore | eval पास |
| **C6** | Frontend: left panel, "New Agent" wizard, list | `npm run dev` में live दिखाना |
| **C7** | Frontend: editor + Save + versions UI | demo + आपकी राय |
| **C8** | Phase 1 gate: agent बने, publish हो, version पढ़ा जाए; `check` + `eval` पास; deploy (आपकी स्पष्ट हाँ के बाद) | deploy का address और `/api/health` |

हर checkpoint पर मैं बताऊँगा: क्या बदला, कैसे आज़माएँ, सीमाएँ, और अगला कदम।

---

## 6. मुझे आपसे अभी चाहिए (A/B/C)

- **Q-R:** R1 (सलाह) / R2 / R3
- **Q-W1:** auto-deploy on push? A) नहीं, repo पर `PORTAL_TOKEN` secret नहीं डालेंगे, deploy मैं `npm run deploy` से आपकी हाँ पर चलाऊँगा **(सलाह)** B) हाँ, हर push पर auto-deploy
- **Q-G:** Git remote का URL (GitHub repo) जो C1 पर जोड़ना है; और क्या वह **public** है? (public हो तो `PATTERNS`/spec में कोई निजी बात नहीं होनी चाहिए)
- **S2–S19:** Status कॉलम में हाँ/बदलाव लिख दें, या "सब मंज़ूर" कह दें।
