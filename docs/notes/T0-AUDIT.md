# T0 audit: teacher platform par Chatwoot Agent Bot adapter

**Context:** Teacher repo (`D:\Github_Codes\class-hosting-platform-github`) का read-only audit, ताकि Chatwoot adapter का flow (webhook → signature + dedupe → तुरंत 200 → async invoke → RAG + LLM → Chatwoot Create Message) तय हो सके। कुछ भी edit/commit/deploy नहीं हुआ। तीन read-only agents ने code पढ़ा (Q8 के लिए Chatwoot के primary sources)। जो code से तय नहीं हुआ वह "unknown" लिखा है।

**Date:** 2026-11-19. यह फ़ाइल teacher repo और Studio, दोनों के बाहर है। कोई implementation नहीं हुआ।

> Repo में "Chatwoot", webhook या बाहरी HMAC का कोई code नहीं है। HMAC सिर्फ़ session/cookie signing में है (`lambda/portal.py`, `student/class_auth.py`)।

---

## Q1. Async self-invoke और deploy का रूप

**Deploy:** एक SAM stack `student-hosting` (`template.yaml`, 1711 लाइनें)। सारे platform functions का `CodeUri: lambda/` है और `Handler` अलग; सब `python3.12`, arm64।

| Function | Handler | Memory MB | Timeout s |
|---|---|---|---|
| GatewayFunction | `gateway.handler` | 256 | 29 |
| JobRunnerFunction | `job_runner.handler` | 128 | 900 |
| StorageFunction | `storage_api.handler` | 256 | 10 |
| KnowledgeFunction | `knowledge_api.handler` | 1024 | 29 |
| KnowledgeWorkerFunction | `knowledge_worker.handler` | 2048 | 900 |
| AiProxyFunction | `ai_proxy.handler` | 256 | 29 |
| DeployFunction | `app.handler` | 1024 | 29 |
| SweeperFunction | `app.sweep` | 512 | 300 |

Student functions `sd-<slug>` runtime पर `lambda/backends.py` `publish()` (L212-257) से बनते हैं: 3008 MB, 27 s। सब `StudentSandboxRole` (template 830-849) पर हैं: सिर्फ़ अपने logs, **`lambda:InvokeFunction` नहीं**। Reserved concurrency किसी पर नहीं (`BACKEND_RESERVED_CONCURRENCY: '0'`, template L258)। Live account limit **unknown** (docs में शुरू में 10; settle: `aws lambda get-account-settings`)।

**IAM आज:** Gateway role `sd-*` और `JobRunnerFunction` को invoke कर सकता है (template 883-887, `RequestResponse`, `gateway.py:157`)। JobRunner सिर्फ़ `sd-*_job` को। KnowledgeFunction → KnowledgeWorkerFunction (L1037)। **कोई function खुद को invoke नहीं कर सकता।**

**Async का मौजूदा pattern है:** `lambda/jobs.py:start()` (L209-243) DynamoDB में `job#<slug>#<id>` लिखकर `InvocationType="Event"` से JobRunner बुलाता है (L236-237); `EventInvokeConfig MaximumRetryAttempts: 0` (template 916-917)। `knowledge.py:438` वही करता है। पर job route Chatwoot के लायक नहीं: per-student `jobs.perDay` 20 (max 1000), concurrent 1 (max 5), platform-wide `INFLIGHT_JOBS_GLOBAL: '2'` (L877), और वह student का code चलाता है।

**सबसे छोटा बदलाव:** नया platform function + अपना `AWS::Serverless::HttpApi` (Storage/Knowledge/AiProxy की नकल), और **receiver + अलग worker** (KnowledgeFunction→KnowledgeWorkerFunction जैसा), `EventInvokeConfig` जानबूझकर सेट। आपके "वही function खुद को invoke करे" वाले flow पर चेतावनी: SAM में `Policies` में `!GetAtt SelfFunction.Arn` role↔function circular dependency बनाता है (यह repo से नहीं, SAM के सामान्य ज्ञान से)। कामचलाऊ तरीक़ा fixed `FunctionName` + `!Sub arn:...`, पर receiver+worker साफ़ है, worker का timeout 900 s तक और IAM अलग scope।

**Guard tests:** `tests/test_template_backend.py` (exact action sets, L50) और `tests/test_template_ai.py:45` (`secretsmanager:` की गिनती 5 पर pinned) नए IAM पर जानबूझकर टूटेंगे और अपडेट होंगे।

## Q2. Webhook entry

**आज ऐसा कोई route नहीं है** जो बिना student token/origin-secret के सिर्फ़ custom HMAC पर बाहरी POST ले। सबसे नज़दीकी नमूने: `StorageApi`, `KnowledgeApi`, `AiProxyApi`: हर एक अलग `HttpApi`, raw `execute-api` URL (CloudFront के बिना), handler के अंदर `Authorization: Bearer` जाँच; throttle 20 या 10 rps।

**Student backends का front door:** browser → CloudFront `/api/*` (`Distribution`, template 705-763) → `BackendRouteFunction` (787-816) `/api/x` को `/b/<slug>/x` बनाता है और `x-viewer-ip = event.viewer.ip` सेट करता है → `BackendApi` `ANY /b/{slug}/{proxy+}` → `GatewayFunction`। `x-origin-secret` असल में `AWS::StackId` का तीसरा हिस्सा है (L736-738), Secrets Manager मान नहीं। Student backends का `RootDomain` ज़रूरी है (वरना 404)।

**`gateway.handler` की जाँचें, क्रम से (`lambda/gateway.py`):**
1. Origin secret (L48-50): `hmac.compare_digest`, वरना 403।
2. Path regex `^/b/<slug>(/.*)?$`, body size।
3. `load_token(slug)` (L106-118): warm-container cache `gateway.cacheSeconds` = 10 s (negative 2 s)।
4. **Per-IP limit (L74-77):** IP = `x-viewer-ip`, वरना `requestContext.http.sourceIp`, वरना `"unknown"`; **`X-Forwarded-For` कभी नहीं पढ़ा जाता।** Key `ip#<slug>#<sha1(ip)[:12]>#<minute>` का DynamoDB `counter_add(limit=gateway.perIpPerMinute, ttl=now+120)` (TokensTable); limit `PER_IP_PER_MINUTE` = 120 (template L262; range 10-10000); ऊपर पर 429 + `retry-after: 30`। Key में slug है, यानी bucket per-student-per-IP है।
5. `/_jobs` routes यहीं (L79-80)।
6. **In-flight limiter (L82-103):** `inflight#global` (cap 3) और `inflight#<slug>` (cap 2) के counters, `leakSeconds` 60 से self-heal, `INFLIGHT_WAIT_MS` 0; slot पूरे synchronous invoke तक रखा जाता है। फिर run allowance `runs#<slug>#<period>` (5000/महीना), फिर `sd-<slug>` invoke।

**एक route को per-IP limit से छूट:** 
- **A (सिफ़ारिश): gateway के बाहर।** नया `HttpApi` + function, route `POST /hooks/chatwoot/{agentId}`। मौजूदा `/b/...` routes को नहीं छूता (API Gateway route से dispatch करता है)। CloudFront ज़रूरी नहीं; Chatwoot को execute-api URL दें (Output `BackendApiUrl` के पास, template L1682)। Auth अपनी: HMAC + timestamp window; throttle और body-size cap भी अपने।
- **B:** मौजूदा `BackendApi` में route जोड़ना (वही default throttle साझा)।
- **C (न करें):** gateway में `if rest.startswith("/hooks/")` L74 से पहले। तब hook को run allowance, in-flight slots और student function की 27 s भी लगेंगी।

## Q3. Data stores

| Store | Key | Conditional write | Item size | TTL |
|---|---|---|---|---|
| `TokensTable` (single table, PK `pk`, template 405-419) | सिर्फ़ `GetItem` by `pk`, कोई GSI नहीं | **हाँ**: `put(if_absent=True)` = `attribute_not_exists(pk)` (`portal.py:92-101`), `replace_if`, atomic `counter_add/sub` | 400 KB | हाँ (`ttl`), PITR |
| `StudentDataTable` (अगर `StorageEnabled`, 423-441) | `s` (`<slug>#<collection>`), `id`; Query चलता है | **नहीं** (`put_value` बिना शर्त) | values ≤ 64 KB (`storage.maxValueKB`, max 380) | **नहीं**; `DeletionPolicy: Retain` |
| `TracesTable` (अगर `TracesEnabled`, 445-487) | `s`, `id`; GSI `by-app`, `by-day` | सिर्फ़ `attribute_exists(s)` वाला update | ~60 KB प्रति item (`traces.maxItemBytes`) | हाँ (30/60/90 दिन) |
| `SitesBucket` S3 (305-349) | prefixes `sites/ portal/ uploads/ archive/ projects/ userdata/<slug>/ knowledge/ worker/` | नहीं | कोई सीमा नहीं | सिर्फ़ lifecycle |

- **Key से query:** StudentData और Traces। **message_id dedupe (conditional write):** सिर्फ़ TokensTable या नई table। **64 KB से बड़ा:** TokensTable/TracesTable (400 KB) और S3।
- **Traces:** TracesTable, key `t#<slug>#<trace_id>` (summary + step items, सब पर `ttl`)।
- **Quality cache और feedback:** teacher-side नहीं। `student/class_quality.py` student के function में class storage (collections `qcache`, `feedback`); cache value ≤ 40 KB, "days" सिर्फ़ record के अंदर का logical age।
- **Knowledge/RAG:** S3 `knowledge/<scope>/<owner>/<collection>/` (`meta.json`, `passages.json.gz`, `vectors.f16`, `index/...`); slot/counter/embedding-cache items (`kdoc# kpass# kwork# emb#`) TokensTable में।
- **Job state:** TokensTable `job#<slug>#<id>` (ttl ≈ 2 दिन), `joblist# jobs# jobsrun#`।

## Q4. Secrets

- **Lambda env (Globals, template 241-276):** `ORIGIN_SECRET` (stack id से), `KEYS_PARAM`, `TEACHER_PARAM`, `AUTH_GOOGLE_PARAM`, `AI_SECRET_NAME` (नाम, मान नहीं), `TOKENS_TABLE`, `SANDBOX_ROLE_ARN`…
- **SSM:** `/student-deploy/keys` (legacy), `/student-deploy/teacher` (login hash + session secret), `/student-deploy/auth-google` (SecureString)।
- **Secrets Manager:** एक secret (`AiSecretName`, default `openrouter`), JSON में `OPENROUTER_MANAGEMENT_KEY`, `GOOGLE_API_KEY`; पाँच functions पढ़ते हैं (Deploy, Sweeper, Knowledge, KnowledgeWorker, AiProxy), पढ़ना `openrouter.py:40`, `gemini.py:66`, `embeddings.py:127`। KMS key सिर्फ़ kit signing के लिए।
- Per-student secrets token item पर (`ga_…`, `ks_…`, `cs_<slug>.<secret>` आदि)।
- **Student env variables:** portal से (`backends.set_env`, L327-347): 20 variables, कुल 4096 bytes, प्रति मान 1024; portal में write-only, पर student का अपना code `os.environ` से पढ़ सकता है।
- **Student code से दूर रखना:** student role SSM/Secrets Manager पढ़ नहीं सकता (permissions boundary)। इसलिए per-client Chatwoot bot secret + API token **platform-side function** में रखें: या हर client का एक Secrets Manager secret (या inbox-keyed JSON map), template में उसी नाम-pattern का नया statement; या TokensTable में encrypted items (पर वह कई roles को readable है, Secrets Manager साफ़ सीमा है)। लागत ≈ $0.40/secret/महीना + API calls, इसलिए container में कुछ मिनट का cache (जैसे `gemini.py`/`openrouter.py`)। `tests/test_template_ai.py:45` की गिनती अपडेट करनी होगी।

## Q5. Reusable core

**ढाँचा:** `student/*.py` student के अपने Lambda में चलता है; teacher runtime से सिर्फ़ HTTP। `lambda/` से कोई import नहीं।

| Helper | स्थिति |
|---|---|
| `class_agent` (`run` L305, `resume` L314, `_loop` L194) | loop पूरा local; model `class_ai.chat` (L160) या injected `model=fn` से। `model=` hook से कहीं भी callable |
| `class_ai` (`chat` L137) | OpenAI-compatible POST; teacher runtime को नहीं छूता (OpenRouter सीधे; Gemini `AI_PROXY_URL/v1` → `lambda/ai_proxy.py`, key `ga_<slug>.<secret>`, `ai_proxy.authenticate` L50-65) |
| `class_knowledge` (`search` L298, `answer` L371) | `CLASS_KNOWLEDGE_URL` पर `Bearer ks_<slug>.<secret>`; handler `lambda/knowledge_api.py:handler` (L98), `knowledge.authenticate` (`knowledge.py` L258-273), `student_owner(item)` → `Owner("student", slug, caps)`; storage `knowledge/student/<slug>/<collection>` |
| `class_quality` | `evaluate` / `evaluate_retrieval` (L164, L199) pure, reusable; `cached`/`feedback` per-student class storage |
| Traces | `lambda/trace_core.py` (pure: privacy levels, masking, `make_span`) सबसे reusable; `traces.record` (`traces.py` L196) सिर्फ़ तब जब `item` में `slug` हो; app-side spans `POST /v1/trace` (`knowledge_api.record_app_spans` L74), `ks_` key से |

**"student/class/site" की धारणा:** student = token item `tok#<sha256>` + `slug` (`^[a-z0-9][a-z0-9-]{0,29}$`, `portal.py` L62); slug ही site, backend Lambda `sd-<slug>`, collection owner और trace app id है। "Class" = पूरा account (एक shared library `Owner("class","library")`, मासिक embedding ceiling `kspend#<month>`)। Agent budgets client-side defaults (6 steps, 20 s, 8000 tokens; `class_agent.py` L41-49); lambda-side `ai.maxTokens` 2048।

**"tenant = business owner, agent = versioned config" को रोकने वाले coupling points:**
1. पहचान = slug (`ks_`, `ga_` keys में embedded; `slug#<slug>` → `tok#<sha>`)। कोई tenant record नहीं; `knowledge_store.SCOPES = ("class","student")` (L36) में tenant scope नहीं; slug ≤ 30 और collection नाम ≤ 40 अक्षर।
2. **Agent या versions की धारणा lambda-side नहीं है:** system prompt/tools student code में; `item["knowledge"]` में सिर्फ़ caps और key।
3. **Shelf quotas per student:** default 5 collections (max 20), 20 documents, 5000 passages, 50 MB (`knowledge.py` L293, `upload_url` L353-375, `_read_book` L541-546); counters `kdoc#student#<slug>` आदि। Versioned shelf के लिए नया scope या नाम-convention (`<agent>-v<n>` 40 अक्षर में)।
4. **Allowance counters per student:** `aiu#<slug>#<period>`, `ksearch#student#<slug>#<period>` (`knowledge.py` L601), `runs#<slug>#<period>`, `trd#<slug>#<day>`। Chatwoot traffic उस student का budget खाएगा।
5. **Cost accounting:** OpenRouter खर्च per-student key से (`ai.refresh_usage`), Gemini `ai_proxy.complete` में, embedding `kspend#` class-wide। Per-tenant/per-agent-version ledger नहीं।
6. **Provider keys:** OpenRouter management key (Secrets Manager `OPENROUTER_MANAGEMENT_KEY`) से हर student के लिए capped key (`ai.set_ai` L226); Gemini `GOOGLE_API_KEY`; embeddings `embeddings._google_key/_openrouter_key` (L116, L123)। Tenant bot को या तो अपनी minted key या proxy + tenant counter।
7. **Kit helpers:** module-global `_trace` (`class_ai.py` L92) से एक process में concurrent runs असुरक्षित; trace flush student key से; ये kit files हैं, बदलने पर CI टूटता है (`CONTRIBUTING.md` L25, "kit 2.x: its contents match the version recorded") और teacher Lambda में पैक भी नहीं होतीं (`CodeUri: lambda/`)।
8. **Lifecycle coupling:** हर call में `portal.status_of(item)=="active"` (`knowledge.py` L268, `ai_proxy.py` L59): student token expire/revoke होते ही tenant बंद।
9. **Traces:** `daily_limit`, `retention_days`, `level` student के token item से; teacher view `app = slug` से group।

## Q6. Studio ↔ teacher runtime auth

| Token | Format (नाम ही) | कैसे बनता / जाँचा जाता | Expiry |
|---|---|---|---|
| Portal token | `mds_…` | `portal.create_token` (L441-458), सिर्फ़ SHA-256 `tok#<sha>`; `student_login` (L757) | `expires_at` ≤ `tokens.maxDays` (365) + grace (`status_of` L330) |
| Personal token | `mdp_…` | `cred#<sha32>`; `access.credential_login` L462, `credential_ok` L477 | `identity.personalTokenDays` (30) |
| Session | `<b64>.<b64 HMAC-SHA256>` | `portal.sign_session` (L304), `verify_session` (L309) | student 12 h, teacher 8 h |
| Legacy deploy key | header `x-deploy-key` | SSM; `lambda/app.py:authenticate` (L113) | नहीं |
| Function keys | `ks_`, `ga_`, OpenRouter key | `knowledge.set_knowledge` (L229), `ai.set_ai` (L246) | token active रहने तक |

- **Scopes नहीं।** पहुँच key के prefix और जाँचने वाले API से तय। एकमात्र RBAC session का `role`।
- **कौन बुला रहा है:** slug key से निकलकर `slug#` → `tok#` item; request body से कभी नहीं।
- **Limits:** DynamoDB atomic counters (`portal.DynamoStore.counter_add`, L139) + API Gateway throttle (knowledge 20 rps, AI proxy 10 rps)।
- **Studio के लिए server-to-server token:** मौजूदा function-key pattern पर `cs_<tenant>.<secret>`। (`cs_` पहले से `trace_core._MASKS` L31 में masking prefix है।) Record `svc#<id>` = key का SHA-256 + अनुमत tenants + scopes (जैसे `cred#`/`inv#` in `access.py`)। नए module में `knowledge.authenticate` जैसा function (constant-time compare, enabled check), जो token item नहीं बल्कि tenant context लौटाए। Chatwoot webhook की auth अलग (Chatwoot bearer नहीं भेजता; Q8 देखें)।
- **बिना teacher बदलाव का विकल्प:** Studio सामान्य student project बनकर अपनी `ks_` और OpenRouter keys से; तब सारे tenants एक student का quota साझा करेंगे, अलगाव सिर्फ़ shelf नामों (`<tenant>-v3`) से, 5-20 collections और 25 MB upload के भीतर।

## Q7. नया code कहाँ रहे

**मौजूदा ढाँचा:** `lambda/` flat (हर concern एक module; सिर्फ़ `lambda/vendor/pypdf` subfolder); हर connector का अपना `HttpApi` + `Function` एक `Condition` के साथ (`KnowledgeApi` L989, `KnowledgeFunction` L997 + LeadingKeys-scoped IAM L1020, `AiProxyApi` L1104)। हर limit `config/settings.json` में, फिर `python tools/gen_settings.py`; `tests/test_settings.py` `lambda/` में numeric literals रोकता है।

**सुझाव (नया deploy unit, वही code bundle):**
- `lambda/chatwoot_api.py` (webhook + tenant/agent admin routes), `lambda/chatwoot_core.py` (pure: payload parse, signature/timestamp जाँच, loop guard, config validation; `trace_core.py` की तरह AWS imports के बिना), `lambda/chatwoot_agents.py` (tenant + versioned agent config), `lambda/chatwoot_runtime.py` (server-side agent loop: **पुनर्लेखन**, import नहीं; `trace_core`, `embeddings`, `knowledge_store` import हो सकते हैं)।
- `template.yaml`: `ChatwootApi` + receiver/worker functions, नई `UseChatwoot` Condition, अपना throttle, संकरा IAM।
- Data: नई छोटी DynamoDB table (dedupe + tenant/agent records, TTL, PITR) बेहतर है। TokensTable पर `DeployFunction`/`SweeperFunction` का `Scan` (template 1306, 1445; `portal.list_prefix` पूरा scan करके filter करता है) रोज़ हज़ारों dedupe items से भारी होगा।
- इससे `app.py`, `portal.py`, `knowledge.py`, `student/` अछूते रहते हैं और webhook burst student functions पर असर नहीं डालता।

**Tests/deploy commands:** `node tests/run.mjs` (Node 22+, Python 3.12, boto3; AWS ज़रूरी नहीं; हर `tests/test_*.py` एक step; `package.json` में scripts नहीं)। नए: `tests/test_chatwoot_core.py`, `test_chatwoot_api.py`, `test_template_chatwoot.py` + `tests/run.mjs` में step; docs `docs/TECHNICAL_SPECIFICATION.md` (`test_spec.py` जाँचता है), `CHANGELOG.md`, `docs/SETTINGS.md`। CI `.github/workflows/ci.yml`: `cfn-lint -i W1030 W2506 -t template.yaml`, `checkov -f template.yaml --config-file .checkov.yaml`, `node tests/run.mjs`। Release `v*` tag = `VERSION`। Deploy: `npx -y <package>.tgz install/plan/apply/update` (`installer/engine.mjs`: package L288, deploy L309; template 51,200 bytes से बड़ा, इसलिए staging S3), या `node setup.mjs`, या `sam deploy` (`samconfig.local.toml`); portal pages `node admin.mjs portal-publish`। लाइव smoke: `python tests/live_smoke.py`।

## Q8. Chatwoot webhook (primary sources)

- **(a) Timeout: 5 सेकंड** (`webhook_timeout`: `WEBHOOK_TIMEOUT` GlobalConfig, खाली या ≤ 0 पर 5; `open_timeout` और `read_timeout` दोनों)। Agent bot के लिए अलग timeout नहीं: `AgentBots::WebhookJob` → `WebhookJob` → `Webhooks::Trigger.execute`। Source: https://raw.githubusercontent.com/chatwoot/chatwoot/develop/lib/webhooks/trigger.rb , https://raw.githubusercontent.com/chatwoot/chatwoot/develop/app/jobs/agent_bots/webhook_job.rb
- **(b) Retry:** agent bot webhooks का हाँ, सामान्य account webhooks का नहीं। `RETRYABLE_AGENT_BOT_STATUSES = [429, 500]` (`trigger.rb`); तब `RetryableError`; job `:high` queue पर, 3 बार, बीच में 3 s (यह retry_on रेखा paraphrase है: exact line पुष्टि करें)। 502/503/504 और (मेरे पढ़ने में) timeout/connection errors **retry नहीं होते**। `X-Chatwoot-Delivery` UUID retries में वही रहता है। अंतिम विफलता (सिर्फ़ `message_created`/`message_updated`) पर `pending` conversation `open` कर दिया जाता है (जब तक `keep_pending_on_bot_failure` न हो) और activity message जुड़ता है।
- **(c) Signature:** headers `X-Chatwoot-Signature: sha256=<hex>`, `X-Chatwoot-Timestamp` (Unix seconds), `X-Chatwoot-Delivery` (UUID)। `"sha256=" + HMAC_SHA256_hex(secret, "<timestamp>.<raw_body>")` (lowercase hex, base64 नहीं; raw bytes पर, JSON दोबारा serialize न करें; constant-time compare; ~5 मिनट से पुराना timestamp reject)। Secret बिना हो तो signature/timestamp headers भेजे ही नहीं जाते। स्रोत `trigger.rb` `request_headers`; docs https://www.chatwoot.com/hc/user-guide/articles/1677693021-how-to-use-webhooks । **Version:** account webhooks का per-webhook secret PR #13573 (v4.12.0); **agent bots और API inbox के लिए signing PR #13892 (v4.13.0)**, `AgentBot` पर अलग `secret` column (`access_token` से अलग)। यानी bot को secret एक release बाद मिला। v4.13.0 से पहले बने bots के पास secret शायद नहीं (unconfirmed), तो उनकी deliveries unsigned। (दूसरे agent ने code से इसे "unknown" कहा था; Chatwoot के source से यह पुष्ट हुआ।)
- **(d) `message_created` payload:** top-level `event`; `id` = message id; `account.id`, `inbox.id` मौजूद; `message_type` payload में string `"incoming"`/`"outgoing"`/`"template"`; `private` boolean; `sender.type` (bot के लिए `agent_bot`; contact/user के लिए docs का schema `contact`/`user`, असली payload में unconfirmed)। `conversation` में docs-sample `display_id` है; plain `conversation.id` भी है या नहीं unconfirmed। Create Message path में dashboard वाला `display_id` इस्तेमाल होता है (मेरी समझ, verify नहीं)। **Loop का जोखिम हाँ:** `webhook_sendable?` incoming, outgoing और template तीनों पर true; इसलिए bot के अपने और agents के outgoing, और private notes भी आते हैं। Handler को `message_type != "incoming"`, `private == true`, `sender.type == "agent_bot"` पर छोड़ना होगा।
- **(e) Create New Message:** `POST /api/v1/accounts/{account_id}/conversations/{conversation_id}/messages`; body `content` (ज़रूरी), `message_type` (`outgoing`/`incoming`), `private`, `content_type`, `content_attributes`, `source_id`, `echo_id`, `template_params`, `attachments[]` (multipart)। Auth: `Authorization: Bearer <token>` (user और agent-bot दोनों **v4.19.0+**); पुराना `api_access_token` header अभी चलता है पर deprecate होने वाला। Bot token से भेजा संदेश bot के नाम से जाता है; user API token से भेजें तो उस user के नाम से। Bot token कौन-से APIs बुला सकता है और क्या किसी भी conversation में लिख सकता है: unconfirmed। इसके स्रोत https://developers.chatwoot.com/api-reference/messages/create-new-message , PR https://github.com/chatwoot/chatwoot/pull/13573 , https://github.com/chatwoot/chatwoot/pull/13892 , `app/listeners/agent_bot_listener.rb`, `app/models/message.rb`, `app/models/concerns/message_filter_helpers.rb` (raw.githubusercontent.com/chatwoot/chatwoot/develop/...)।
- **सावधानी:** Q8 के कुछ अंश fetch-summaries से paraphrase हुए हैं (retry_on की exact लाइन, timeout/connection errors का retry)। Implementation से पहले उन्हें raw file से दोबारा पढ़ लें।

---

## जोखिम सूची
1. **Quota capture:** अगर adapter किसी student के slug/key से चलाया, तो bot traffic उसके AI, search और trace allowances खाएगा और token expiry bot को चुप कर देगी।
2. **Cost attribution नहीं:** per-tenant/per-agent-version ledger नहीं; class-wide `kspend#` और साझा management key से एक tenant साझा embedding खर्च खत्म कर सकता है।
3. **Account concurrency:** docs में सीमा 10 थी; async worker + RAG + LLM gateway और student functions से होड़ करेंगे (alarm threshold 8)। लाइव limit unknown।
4. **Ack के बाद की विफलता Chatwoot को दिखती नहीं:** Chatwoot सिर्फ़ 429/500 पर retry करता है। 200 देने के बाद worker गिरा तो संदेश खो जाएगा; अपना retry/dead-letter/status चाहिए (साथ में Chatwoot की अपनी `open` में बदलने वाली विफलता-रेखा तभी चलती है जब उसकी delivery फेल हो)।
5. **Duplicate reply:** async Lambda at-least-once है; `jobs.run()` की `status == "queued"` जाँच के बाद unconditional update (`jobs.py:337-340`) दोहरे run की गुंजाइश देता है। `EventInvokeConfig` न हो तो 2 अपने-आप retries; Chatwoot संदेश भेजने के बाद retry = दोहरा जवाब। इसलिए atomic "claim" write (`attribute_not_exists`) चाहिए, और Chatwoot की अपनी retries (429/500, वही delivery id) के लिए भी dedupe।
6. **Student-side secrets:** token/HMAC secret student env में रखे तो student code पढ़ सकता है (20 vars, 4 KB की सीमा भी); platform-side function में रखें।
7. **`ORIGIN_SECRET` rotate नहीं होता** (stack id का हिस्सा, function config और CloudFront origin में दिखता है, सब functions में साझा)।
8. **TokensTable पर scan का बोझ** (Deploy/Sweeper का `Scan`) और TTL deletion की देरी, इसलिए dedupe के लिए अलग table।
9. **Traces में निजी डेटा:** traces slug से keyed हैं और default `metadata` में text नहीं रखता; Chatwoot ग्राहक संदेशों में personal data होता है, tenant का trace level/retention अलग सोचना होगा।
10. **Kit drift:** `student/` या `starter/` बदलने पर kit-version test टूटता है; adapter उन्हें न छुए।
11. **Guard tests जानबूझकर टूटेंगे** (नया IAM) और `checkov` चौड़े grants पर रोकेगा।
12. **Loop:** bot के अपने outgoing संदेश वापस आते हैं (Q8-d)।
13. **Bearer token के लिए Chatwoot ≥ 4.19.0** चाहिए; आपका version unknown है। पुराने bots (< 4.13.0) unsigned हो सकते हैं।
14. **Unknowns:** live Lambda account limit (`aws lambda get-account-settings`); deployed `BackendApiRate/Burst`, और stack में `BackendEnabled/KnowledgeEnabled/TracesEnabled` की स्थिति (`aws cloudformation describe-stacks`); Chatwoot का bot-token permission scope और `conversation.id` बनाम `display_id`।

## सुझाया गया न्यूनतम बदलाव (अभी implementation नहीं)
1. **`template.yaml`:** `UseChatwoot` Condition के साथ `ChatwootApi` (अपना `HttpApi`, अपना throttle) + `ChatwootReceiverFunction` (≈256 MB, कुछ सेकंड) + `ChatwootWorkerFunction` (≥1024 MB, timeout ज़रूरत भर, ≤ 900 s) + स्पष्ट `EventInvokeConfig` (retries 0 या 1, सोच-समझकर)। Receiver को सिर्फ़ worker पर `lambda:InvokeFunction`। Output में webhook URL।
2. **नई DynamoDB table** (PITR, TTL, `attribute_not_exists` dedupe): `message_id` dedupe + tenant, agent और version records।
3. **Secrets:** हर client का एक Secrets Manager secret (bot HMAC secret + API token) या inbox-keyed JSON; सिर्फ़ receiver/worker पढ़ें; container में छोटा cache। `test_template_ai.py:45` की गिनती जानबूझकर अपडेट।
4. **नए modules:** `lambda/chatwoot_core.py` (pure), `chatwoot_api.py`, `chatwoot_agents.py`, `chatwoot_runtime.py`। `trace_core`, `embeddings`, `knowledge_store` import; `student/*` और `class_agent` नहीं।
5. **Tenant का knowledge और cost:** (a) `knowledge_store` में नया "tenant" scope और per-tenant counters (साफ़, पर teacher-core बदलाव), या (b) शुरुआत में एक समर्पित service-student का slug (बिना teacher बदलाव, पर साझा quota; Risk 1)।
6. **Studio auth:** `cs_<tenant>.<secret>` service key, `svc#<id>` record, नया `authenticate` जो tenant context दे।
7. **Webhook route:** `POST /hooks/chatwoot/{agentId}`: raw body पर HMAC (`<timestamp>.<raw_body>`, constant-time), timestamp window, body-size cap, `message_type=="incoming"` और `private==false` और `sender.type != "agent_bot"` का loop guard, `message_id` (और delivery id) पर dedupe; फिर 200, फिर worker को async।
8. **ज़रूरी हिस्सा:** हर limit `config/settings.json` में + `gen_settings.py`; tests (`test_chatwoot_core.py`, `test_chatwoot_api.py`, `test_template_chatwoot.py`, `tests/run.mjs` step); `docs/TECHNICAL_SPECIFICATION.md`, `CHANGELOG.md`, `docs/SETTINGS.md`।

## आपसे चाहिए फ़ैसले (implementation से पहले)
- **Chatwoot version** (≥ 4.19 के लिए Bearer, ≥ 4.13 के लिए agent-bot signing)।
- **Reply किस token से:** bot token या user API token।
- **Knowledge की मालिकी:** नया tenant scope (teacher core बदलेगा) बनाम service-student (साझा quota)।
- **Live AWS Lambda concurrency limit** (account का `get-account-settings`)।

## Verification (इस plan का)
- Approval के बाद सिर्फ़ `studio-notes\T0-AUDIT.md` लिखना; फिर `ls`/आकार से जाँचना कि फ़ाइल बनी, और teacher repo में कोई बदलाव नहीं (`git status` वहाँ साफ़ ही रहे, जो मैं सिर्फ़ पढ़कर जाँचूँगा)।
- Studio का `git status` अब भी वही 10 बदलाव दिखाए (C2 अछूता)।
