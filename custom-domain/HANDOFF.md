# STATUS (2026-10-20): ported, deployed and verified live

Ported into this project and deployed with the safe routine (8 in-place modifications, no adds or removals). Verified in production with a real domain (`test.tripnaama.com`): certificate, HTTPS, redeploy reaching the custom domain, and removal. The hand-made test tenant `dt_3Kx75dP05KtCYq2S1o7o05xUBar` was deleted for that test; the `test` CNAME at Cloudflare is still in place. Documentation moved to `implement.md`, Part 3. The reference files in this folder are kept only for history; `tenant-distribution.json` and `spa-fallback.js` still describe the AWS resources that live outside the template.

---

# Custom domains for students: handoff notes

Goal: a teacher switch (on/off per student). When ON, the student can enter their own domain on the student
portal page; when OFF the field is disabled with "Owner not enabled for you. Please contact site owner."
One custom domain per student. Student adds one CNAME at their own DNS host; HTTPS is automatic.

## Source of truth
THIS folder (`D:\Github_Codes\student-deploy`) is the project deployed to the AWS stack `student-hosting`
(us-east-1, account <ACCOUNT_ID>). Do NOT run `sam deploy` from any other copy: a different template
deletes the portal, tokens table and sweeper (this happened once on 2026-10-20 and was restored).

Safe deploy routine: `sam deploy --no-execute-changeset --no-confirm-changeset`, then
`aws cloudformation describe-change-set ...` and read every row (no Remove/Replace you did not expect), then
`aws cloudformation execute-change-set`.

## What already exists in AWS (created by hand with the CLI, NOT in template.yaml yet)
| Thing | Value |
|---|---|
| CloudFront function (SPA fallback) | `student-hosting-spa-fallback` (source: spa-fallback.js) |
| Multi-tenant distribution (template) | `<TENANT_DISTRIBUTION_ID>` (config: tenant-distribution.json); origin path `/sites/{{student}}` |
| Connection group routing endpoint | `<id>.cloudfront.net` (students CNAME to this) |
| Test tenant | `dt_3Kx75dP05KtCYq2S1o7o05xUBar`, domain `test.tripnaama.com`, student param `smoketest` |
| Test DNS (Cloudflare, DNS only) | `test` CNAME -> `<id>.cloudfront.net`. Never touch `tripnaama.com` itself |

The test tenant currently returns 403: this project's bucket policy only allows `Distribution` and
`PortalDistribution`. Fix when porting: allow the multi-tenant distribution ARN (or `distribution/*` in the account).

## Verified facts (spike, 2026-10-20)
- `create-distribution-tenant` fails (`InvalidArgument ... Could not verify Domain Name ownership`) until the
  domain's CNAME already points at the routing endpoint. So the flow is: student adds CNAME -> create tenant.
- With `ManagedCertificateRequest {ValidationTokenHost: cloudfront, PrimaryDomainName: <domain>}` CloudFront issues
  the certificate over HTTP (no validation DNS record). It is created as a *pending* cert; it must then be applied with
  `update-distribution-tenant --customizations Certificate={Arn=...}` (get the ARN from
  `get-managed-certificate-details`). After that the domain becomes `active` and the site serves over HTTPS.
- Bare/apex domains cannot use a CNAME (only works if their DNS flattens CNAMEs, e.g. Cloudflare). Recommend `www.`.
- Cloudflare records for CloudFront must be DNS only (grey cloud).
- Tenant pricing is unverified (community report: first 10 tenants free). Check the billing console.
- Deleting a tenant: disable first, wait until `Deployed`, then delete.

## Reference code (tested, not deployed)
- `app.domain-routes.reference.py`: Lambda functions `me`, `set_domain`, `domain_status` (state machine driven by polling),
  `delete_domain`, `validate_domain`, DynamoDB helpers. Written against a DynamoDB table `StudentSettings`; adapt to this
  project's `TokensTable` / `lambda/portal.py` session model instead of the `x-deploy-key` auth used there.
- `test_domains.reference.py`: in-memory fakes of DynamoDB + CloudFront; covers off-switch (403), validation,
  waiting_dns -> issuing_certificate -> finishing -> live, and removal.
- `portal-page.reference.html`: a standalone domain card (disabled state, form, 4-step progress, CNAME instructions).
  Port the UI into `web/student.html` / `web/student.js`, and add the teacher switch to `web/teacher.*`.

## Port plan
1. template.yaml: Lambda env `TENANT_DISTRIBUTION_ID=<TENANT_DISTRIBUTION_ID>`, `ROUTING_ENDPOINT=<id>.cloudfront.net`;
   IAM for `cloudfront:*DistributionTenant*`, `GetManagedCertificateDetails`, `acm:*Certificate*`; bucket policy
   widened for the multi-tenant distribution; a place to store per-student settings (extend TokensTable or add a table).
2. lambda/portal.py: student session routes `GET /api/student/domain`, `PUT`, `DELETE`, `GET .../status`
   (enforce the teacher switch server-side), teacher route to toggle.
3. web/: teacher switch + student domain card.
4. tests: port test_domains; keep `node tests/run.mjs` green.
5. Review the changeset row by row before executing.

## Credentials
`teacher-login.local` (git-ignored) holds the portal teacher username/password set on 2026-10-20.
Change with `node admin.mjs teacher-setup <username>`.
