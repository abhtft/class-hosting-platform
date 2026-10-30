# Security

## Reporting a problem

Please report security problems **privately**, not as a public issue: use GitHub's [Report a vulnerability](https://github.com/abhtft/class-hosting-platform/security/advisories/new) form (Security tab of this repository). Include what you found, how to reproduce it, and which version (`VERSION` file). Please do not include real student data, passwords, keys or account numbers.

**What to expect.** This is a volunteer-run project, so these are targets, not guarantees: an acknowledgement within **3 days**, an assessment within **7 days**, and a fix or a mitigation for a confirmed vulnerability within **30 days** (sooner for anything that exposes student data or credentials). We will tell you when it is fixed and, if you wish, credit you in the release notes. Please give us that time before disclosing publicly (coordinated disclosure). A fix is published as a new release; the teacher guide explains how to update.

## What this project is, security-wise

Each teacher runs their **own copy** in **their own AWS account**; there is no shared service run by the maintainers. A flaw therefore affects the teacher's account and their students, which is why the design is conservative:

- Students never get AWS access; they get a revocable token. Teacher login is a hashed password (scrypt) with signed sessions.
- Student backends run in a sandbox role with a permission boundary; AI keys given to students are capped and revocable; the platform's own keys live in AWS Secrets Manager and are never put in a student's function.
- The installer and the teacher MCP never take passwords or API keys through an editor, never delete the stack, and refuse any update that would replace a data-holding resource.
- Releases are built by CI from a tag, with build provenance and published checksums (`sha256sums.txt`).

Details: `docs/TECHNICAL_SPECIFICATION.md` (security model) and `docs/AWS_LEARNINGS.md`.

## Automated checks

Every push runs the test suite, `cfn-lint` and a `checkov` security scan of the infrastructure template (`template.yaml`). The exceptions to that scan are listed with a reason each in `.checkov.yaml`; anything not listed must pass. GitHub CodeQL analyses the JavaScript, Python and workflow files, the OpenSSF Scorecard workflow scores the repository's practices, and Dependabot proposes dependency updates weekly. A scan of the whole git history found no keys, tokens or account numbers. These are automated checks, not a certification or a third-party review.

## Verifying a release

Releases are built by CI from a tag, with build provenance and a `sha256sums.txt`. From the first release that has them, each release file also has a **Sigstore signature** (a `.sigstore.json` file next to it, made without any key we hold: it is tied to this repository's release workflow and recorded in a public log). To check a download:

```bash
cosign verify-blob   --bundle sha256sums.txt.sigstore.json   --certificate-identity "https://github.com/abhtft/class-hosting-platform/.github/workflows/release.yml@refs/tags/v<version>"   --certificate-oidc-issuer https://token.actions.githubusercontent.com   sha256sums.txt
sha256sum -c sha256sums.txt
```

The first command proves the checksums file was produced by this project's release workflow; the second proves your download matches it. (Replace `<version>`; `cosign` is at https://github.com/sigstore/cosign.)

## Supported versions

Only the newest release receives fixes. Teachers update with `plan` and `apply` (see the teacher guide); the student kit then follows automatically. This project is a **public beta** and has had **no third-party security review**.

## For teachers

Use an IAM user with MFA for the install, delete its access key afterwards, keep the AWS Budget on, and apply releases promptly (`status` tells you when one is out).
