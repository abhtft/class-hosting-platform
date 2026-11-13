# Reading list, videos, books and glossary

> Order matters. Items marked ★ are the ones I would read/watch first. Where I give a "search:" phrase instead of a link, I'm not sure of a stable URL: search for the exact title.

## Link check

All `https://` links in this folder were requested with `curl` on 2026-11-13. The result is recorded in `LINK-CHECK.md` in this folder (OK / redirected / failed / blocked by the site's bot protection). A "blocked" result usually means the page exists but refuses automated requests; open it in a browser.

## 1. Start here (the mental models)

| Topic | Resource |
|---|---|
| ★ Serverless architecture, 30 minutes | AWS docs: [What is serverless?](https://aws.amazon.com/serverless/) and [Lambda developer guide](https://docs.aws.amazon.com/lambda/latest/dg/welcome.html) |
| ★ AWS Well-Architected | [Framework overview](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html) |
| ★ SaaS / multi-tenant | [SaaS Lens](https://docs.aws.amazon.com/wellarchitected/latest/saas-lens/saas-lens.html), [Fairness in multi-tenant systems](https://aws.amazon.com/builders-library/fairness-in-multi-tenant-systems/) |
| ★ Distributed systems habits | AWS Builders' Library: [timeouts, retries, backoff with jitter](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/), [load shedding](https://aws.amazon.com/builders-library/using-load-shedding-to-avoid-overload/), [idempotent APIs](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/) |
| Twelve-Factor App | <https://12factor.net/> |

## 2. AWS service deep dives

| Service | Read | Watch (search on YouTube) |
|---|---|---|
| Lambda | [Execution environment](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtime-environment.html), [Concurrency](https://docs.aws.amazon.com/lambda/latest/dg/lambda-concurrency.html), [Quotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html) | "AWS re:Invent Lambda under the hood", "Serverless Land concurrency" |
| API Gateway | [HTTP APIs](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api.html), [HTTP vs REST](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-vs-rest.html) | "API Gateway HTTP API vs REST API" |
| CloudFront | [Developer guide](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/Introduction.html), [CloudFront Functions](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/cloudfront-functions.html) | "AWS re:Invent CloudFront deep dive" |
| S3 | [User guide](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html), [Presigned URLs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html) | "S3 presigned URLs explained" |
| DynamoDB | [Developer guide](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Introduction.html), [Best practices](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/best-practices.html) | Rick Houlihan: "Advanced Design Patterns for DynamoDB" |
| IAM | [Introduction](https://docs.aws.amazon.com/IAM/latest/UserGuide/introduction.html), [Policy evaluation logic](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html), [Permission boundaries](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_boundaries.html) | "AWS re:Inforce IAM policy evaluation" |
| KMS | [Overview](https://docs.aws.amazon.com/kms/latest/developerguide/overview.html) | "AWS KMS deep dive" |
| Cognito | [What is Cognito](https://docs.aws.amazon.com/cognito/latest/developerguide/what-is-amazon-cognito.html), [Verify a JWT](https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-tokens-verifying-a-jwt.html) | "Amazon Cognito user pools explained" |
| CloudFormation / SAM | [User guide](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/Welcome.html), [Change sets](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-cfn-updating-stacks-changesets.html), [SAM](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/what-is-sam.html) | "CloudFormation change sets" |
| CloudWatch / SNS | [CloudWatch](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/WhatIsCloudWatch.html), [Logs filter syntax](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/FilterAndPatternSyntax.html) | "CloudWatch Logs Insights" |
| Systems Manager / Secrets | [Parameter Store](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html), [Secrets Manager](https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html), [Session Manager](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html) | — |

Also in this repo: `docs/AWS_LEARNINGS.md` has ten exercises (section 15) and a log of real mistakes (section 14). Do the exercises.

## 3. Web fundamentals (browser security, HTTP, DNS)

| Topic | Resource |
|---|---|
| ★ HTTP and web security | [MDN Web Security](https://developer.mozilla.org/en-US/docs/Web/Security), [MDN CORS](https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS), [MDN Same-origin policy](https://developer.mozilla.org/en-US/docs/Web/Security/Same-origin_policy), [MDN Cookies](https://developer.mozilla.org/en-US/docs/Web/HTTP/Cookies) |
| DNS | [How DNS works (comic)](https://howdns.works/), [Cloudflare: What is DNS](https://www.cloudflare.com/learning/dns/what-is-dns/) |
| Caching | [web.dev HTTP cache](https://web.dev/articles/http-cache) |
| PWA | [web.dev PWA course](https://web.dev/learn/pwa) |
| OWASP | [Top 10](https://owasp.org/www-project-top-ten/), [Cheat sheets](https://cheatsheetseries.owasp.org/) |

## 4. Identity and cryptography

| Topic | Resource |
|---|---|
| ★ OAuth 2.0 / OIDC | [oauth.com (free)](https://www.oauth.com/), [OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html), [RFC 7636 PKCE](https://datatracker.ietf.org/doc/html/rfc7636) |
| JWT | [jwt.io introduction](https://jwt.io/introduction), [RFC 7519](https://datatracker.ietf.org/doc/html/rfc7519) |
| Passwords | [OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html), [RFC 7914 scrypt](https://datatracker.ietf.org/doc/html/rfc7914) |
| HMAC / timing attacks | [RFC 2104](https://datatracker.ietf.org/doc/html/rfc2104), [A Lesson in Timing Attacks](https://codahale.com/a-lesson-in-timing-attacks/) |
| Practical cryptography | [Practical Cryptography for Developers](https://cryptobook.nakov.com/) |
| Passkeys | [webauthn.guide](https://webauthn.guide/) |

## 5. Supply chain, CI and releases

| Topic | Resource |
|---|---|
| Sigstore | [docs.sigstore.dev](https://docs.sigstore.dev/) |
| SLSA | [slsa.dev](https://slsa.dev/) |
| OpenSSF | [Scorecard](https://securityscorecards.dev/) |
| GitHub Actions security | [Security hardening](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions), [Artifact attestations](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations) |
| Versioning | [semver.org](https://semver.org/), [Keep a Changelog](https://keepachangelog.com/) |
| npm | [package-lock](https://docs.npmjs.com/cli/v10/configuring-npm/package-lock-json), [npx](https://docs.npmjs.com/cli/v10/commands/npx) |

## 6. MCP and agents

| Topic | Resource |
|---|---|
| ★ MCP | [modelcontextprotocol.io](https://modelcontextprotocol.io/), [Specification](https://modelcontextprotocol.io/specification), [TypeScript SDK](https://github.com/modelcontextprotocol/typescript-sdk), [JSON-RPC 2.0](https://www.jsonrpc.org/specification) |
| ★ Agents | [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents), [Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents), [Lilian Weng](https://lilianweng.github.io/posts/2023-06-23-agent/) |
| Security | [OWASP GenAI Top 10 for LLM apps](https://genai.owasp.org/llm-top-10/), [Simon Willison on prompt injection](https://simonwillison.net/series/prompt-injection/) |
| Context engineering | [Anthropic: Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) |

## 7. LLMs and RAG

| Topic | Resource |
|---|---|
| ★ How LLMs work (video) | Andrej Karpathy, "Intro to Large Language Models" (YouTube, 1 hour): <https://www.youtube.com/watch?v=zjkBMFhNj_g> |
| Tokenizers (video) | Andrej Karpathy, "Let's build the GPT Tokenizer": <https://www.youtube.com/watch?v=zduSFxRajkE> |
| Transformers (video) | 3Blue1Brown, "But what is a GPT?": <https://www.youtube.com/watch?v=wjZofJX0v4M> |
| Transformers (text) | [The Illustrated Transformer](https://jalammar.github.io/illustrated-transformer/) |
| ★ RAG | [RAG paper](https://arxiv.org/abs/2005.11401), [Pinecone Learning Center](https://www.pinecone.io/learn/), [Anthropic Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval) |
| Search theory | [Stanford IR book](https://nlp.stanford.edu/IR-book/), [Elastic: Practical BM25](https://www.elastic.co/blog/practical-bm25-part-2-the-bm25-algorithm-and-its-variables), [RRF paper](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) |
| Embedding models | [MTEB leaderboard](https://huggingface.co/spaces/mteb/leaderboard) |
| Evals | [Hamel Husain: Your AI Product Needs Evals](https://hamel.dev/blog/posts/evals/), [Ragas](https://docs.ragas.io/), [Eugene Yan: LLM patterns](https://eugeneyan.com/writing/llm-patterns/) |
| Provider docs | [Anthropic](https://docs.anthropic.com), [OpenAI API](https://platform.openai.com/docs/api-reference/chat), [Gemini API](https://ai.google.dev/gemini-api/docs), [OpenRouter](https://openrouter.ai/docs) |

## 8. Observability and operations

| Topic | Resource |
|---|---|
| ★ OpenTelemetry | [Concepts](https://opentelemetry.io/docs/concepts/), [Traces](https://opentelemetry.io/docs/concepts/signals/traces/) |
| SRE | [Google SRE Book (free)](https://sre.google/sre-book/table-of-contents/) |
| LLM tracing tools | [Langfuse](https://langfuse.com/docs), [Arize Phoenix](https://docs.arize.com/phoenix) |
| Builders' Library | [Instrumenting distributed systems](https://aws.amazon.com/builders-library/instrumenting-distributed-systems-for-operational-visibility/) |

## 9. Testing and engineering practice

| Topic | Resource |
|---|---|
| ★ Testing | [Practical Test Pyramid](https://martinfowler.com/articles/practical-test-pyramid.html), [Test Double](https://martinfowler.com/bliki/TestDouble.html), [Mocks aren't stubs](https://martinfowler.com/articles/mocksArentStubs.html) |
| Dependency injection | [Fowler: Inversion of Control containers and the DI pattern](https://martinfowler.com/articles/injection.html) |
| Decisions | [Nygard: Documenting architecture decisions](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions) |

## 10. Books (in the order I would buy them)

1. *Designing Data-Intensive Applications* — Martin Kleppmann (state, consistency, delivery guarantees).
2. *The DynamoDB Book* — Alex DeBrie (<https://www.dynamodbbook.com/>).
3. *AI Engineering* — Chip Huyen (LLM apps, evals, RAG, agents).
4. *Observability Engineering* — Majors, Fong-Jones, Miranda.
5. *Threat Modeling* — Adam Shostack.
6. *Release It!* — Michael Nygard (failure modes, stability patterns).
7. *Production-Ready Serverless* — Yan Cui.

## 11. YouTube searches that work well

- "AWS re:Invent serverless" (any year; start with talks by Chris Munns and James Beswick)
- "Hussein Nasser" for networking/HTTP/databases fundamentals
- "ByteByteGo" for system design diagrams
- "Anthropic" channel for agents and MCP talks
- "Simon Willison prompt injection talk"
- "Fireship OAuth in 100 seconds" and "JWT in 100 seconds"

## 12. Glossary (one line each)

| Term | Meaning |
|---|---|
| ACM | AWS Certificate Manager: issues TLS certificates |
| Alias (CloudFront) | A domain name a distribution answers to |
| ARN | Amazon Resource Name, a unique id of an AWS resource |
| BM25 | Keyword ranking function |
| CNAME | DNS alias record |
| Control plane / data plane | Managing resources / serving traffic |
| Cosine similarity | Angle-based similarity of vectors |
| CORS | Browser rule for cross-origin requests |
| DLQ | Dead-letter queue |
| Embedding | Vector of numbers representing meaning |
| ESM | ECMAScript modules (`import`/`export`) |
| Idempotent | Safe to repeat |
| IaC | Infrastructure as code |
| JSON-RPC | Tiny request/response protocol MCP uses |
| JWT | Signed JSON token |
| JWKS | A set of public keys for verifying JWTs |
| KMS | AWS Key Management Service |
| MCP | Model Context Protocol |
| MRR | Mean reciprocal rank (retrieval metric) |
| MAU | Monthly active user (Cognito pricing) |
| OAC | Origin Access Control (CloudFront → private S3) |
| OIDC | OpenID Connect (login layer on OAuth) |
| PITR | Point-in-time recovery (DynamoDB) |
| PKCE | Proof key for code exchange |
| RAG | Retrieval-augmented generation |
| RRF | Reciprocal rank fusion |
| SAM | Serverless Application Model |
| SSM | AWS Systems Manager (here: Parameter Store) |
| TOFU | Trust on first use |
| TTL | Time to live (auto-expiry) |
| XSS / CSRF / SSRF | Script injection / forged requests / server-side request forgery |
