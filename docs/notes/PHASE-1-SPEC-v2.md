# Agent Studio — Phase 1 spec v2 (मौजूदा और अंतिम)

**Date:** 2026-11-19
**यह फ़ाइल पुरानी `PHASE-1-SPEC-FINAL.md` और `PHASE-1-SPEC.md` की जगह लेती है।** अगर दोनों में फ़र्क हो, तो यही सही है।
**Portal:** https://deploy.margdarshakss.com
**Project revision (C1 के बाद):** 1, local commit `9fa72c0`।

---

## 1. फ़ैसले (सब तय)

| ID | फ़ैसला |
|---|---|
| D1 | Purposes: chatbot के लिए `sales`, `lead_collection`, `support`, `faq`; general के लिए `assistant`, `custom` |
| D2 | सिर्फ़ एक owner, Google sign-in (`class_auth`) |
| D3 | Studio UI सिर्फ़ English। Customer से बातचीत की भाषा हर agent की `language` (`hi` / `en` / `hinglish`) से, Phase 4 में |
| D4 | हर version अलग record: `agents` (agent) और `agent-versions` (version) |
| D5 | Secrets portal env variables में। Phase 1 में कोई secret नहीं |
| D6 | Starter kit रहेगा। Demo हटेगा (नीचे C2 देखें) |
| D7 | GitHub नहीं। Sync सिर्फ़ class-platform MCP: `push_project`, `pull_project` |
| D8 | Git सिर्फ़ local, कोई remote नहीं |
| D9 | `project_auto` बंद रखें (नीचे सिफ़ारिश देखें) |
| D10 | `deploy` सिर्फ़ आपकी स्पष्ट हाँ पर, `confirm=true` के साथ |
| D11 | `.temp/` (planning) project फ़ोल्डर से बाहर: `D:\Github_Codes\Test\studio-notes\` |
| D12 | `KIT-PIN.md` नहीं बनेगा। Kit version `status` से देखेंगे |
| D13 | सीधे main पर काम। हर मंज़ूर checkpoint के बाद push (dry_run पहले)। Force-push कभी नहीं |

## 2. पूरे हो चुके checkpoints

| # | परिणाम |
|---|---|
| C0 | मंज़ूर (आपके Q और O जवाब) |
| C1 | पूरा। Local commit `9fa72c0` (84 फ़ाइलें, कोई remote नहीं)। Platform पर 29 फ़ाइलें गईं, revision 1 (124 KB)। `.env.local`, `.class-project.json`, `.kit-backup/`, `node_modules/`, `.temp/` और managed kit फ़ाइलें नहीं गईं। `npm run deploy` नहीं चला |

## 3. खुले checkpoints (क्रम यही है; हर एक पर रुकना है)

| # | काम | Gate |
|---|---|---|
| **C2** | Starter की सफ़ाई और खाली shell (नीचे §4) | `npm run check` साफ़; `npm run dev` में खाली shell |
| **C3** | Sign-in: local "Local Visitor"; platform पर `signin -- on` (आपकी हाँ पर) | `/auth/me` जवाब; बिना sign-in 401 |
| **C4** | Agents CRUD, validation, ownership (दूसरे का agent = 404), 409 (`updated_at`) | `npm run eval` पास |
| **C5** | Publish और versions: हर publish एक `agent-versions` record बनाए; restore = नया draft | `npm run eval` पास |
| **C6** | Left panel, "New Agent" wizard (Type, Purpose), list | `npm run dev` में दिखाना |
| **C7** | Editor, Save, versions UI, unsaved चेतावनी | Demo और आपकी राय |
| **C8** | Phase 1 gate: agent बने, publish हो, version पढ़ा जाए। `check` और `eval` पास। फिर `deploy` (आपकी हाँ पर) | `/api/health` |

हर checkpoint पर: `check_project` → `npm run eval` (जहाँ लागू) → `push_project dry_run` की सूची दिखाना → आपकी हाँ → push।

## 4. C2 का दायरा (Starter सफ़ाई)

**हटाना (student demo):** `backend/study.py`, `frontend/src/StudyPanel.tsx`, `frontend/src/AgentPanel.tsx`, `frontend/src/patterns.ts`, `evals/study.json`, और `backend/app.py` की starter routes (`/hello`, `/echo`, `/ask`, `/notes`, `/private`)। `app.py` से `import study` और `study.route(...)` भी।

**रखना:** `/health`, `class_auth.handle`, `run_job` ढाँचा।

**बदलना:** `frontend/src/App.tsx` (खाली Studio shell), `README.md`, `frontend/src/styles.css`, `frontend/index.html`।

**कभी न छूना (managed):** `class_*.py`, `tools/`, `portal-deploy.mjs`, `platform.json`, `kit-manifest.json`, `.github/workflows/deploy.yml`, `variants/`, kit docs।

## 5. मौजूदा storage और सीमाएँ

- Class storage: value ≤ 64 KB, कोई SQL नहीं, कोई search या sort नहीं, दो writer में आख़िरी जीतता है। इसलिए `updated_at` वाला 409 ज़रूरी है।
- Agent record: `{id, owner, name, description, type, purpose, language, instructions, draft_updated_at, published_version}`। `instructions` ≤ 8000 अक्षर, लेकिन UTF-8 में 64 KB से छोटा रहे।
- Version record: `{id: "<agentId>.<n>", agentId, n, published_at, snapshot}`। हटाना नहीं।
- Gateway: 27 s, custom domain बंद, Python stdlib, कोई pip package नहीं।

## 6. Hard constraints

1. Layout `frontend/` और `backend/`; नाम नहीं बदलेंगे।
2. Backend सिर्फ़ Python 3.12 stdlib, एक function।
3. Page सिर्फ़ `fetch('/api/...')`; कोई localhost या पूरा URL नहीं।
4. Browser में कोई secret नहीं।
5. Managed फ़ाइलें नहीं छुएँगे (§4)।
6. अपना login, password या JWT नहीं। सिर्फ़ `class_auth`।
7. `.env.local` कभी नहीं खोला, पढ़ा या print किया जाएगा। Token सिर्फ़ आप `npm run login` से देंगे।
8. Phase 1 में AI, knowledge, Chatwoot, webhook, leads नहीं।
9. `deploy` सिर्फ़ आपकी हाँ पर। Auto-deploy कभी नहीं।
10. `check` की कोई ✖ चुप या बाइपास नहीं होगी।
11. `push_project` (confirm=true) और `deploy` सिर्फ़ आपकी स्पष्ट हाँ पर।

## 7. MCP नियम (class-platform)

- पहले `dry_run=true`, फिर सूची दिखाना, फिर हाँ।
- `init_project` इस्तेमाल न करें (फ़ोल्डर खाली नहीं है)।
- `project_auto`: C1 के push से चालू हो गया है। **सिफ़ारिश: बंद करें।** Deploy हाथ से ही होता है, इसलिए यह ज़रूरी नहीं, पर बंद रहने से कुछ अनचाहा नहीं होगा।
- `logs`: deploy के बाद जाँच के लिए।

## 8. अभी खुले सवाल

- **Q-S1:** `project_auto` अभी बंद करें? (सिफ़ारिश: हाँ)
- **Q-S2:** `.temp` की दोनों फ़ाइलों की कॉपी `studio-notes` में है? (C1 में पुष्टि हुई थी; आप कन्फ़र्म करें)
- **Q-S3:** S16 (add-ons: frontend-design, responsive, accessibility) अभी चाहिए या C6 के समय? (सिफ़ारिश: C6 पर)
