# Security

## Reporting a problem

Please report security problems **privately**: use GitHub's "Report a vulnerability" (Security tab of this repository) rather than a public issue. Include what you found, how to reproduce it, and which version (`VERSION` file). You will get an answer within a few days.

## What this project is, security-wise

Each teacher runs their **own copy** in **their own AWS account**; there is no shared service run by the maintainers. A flaw therefore affects the teacher's account and their students, which is why the design is conservative:

- Students never get AWS access; they get a revocable token. Teacher login is a hashed password (scrypt) with signed sessions.
- Student backends run in a sandbox role with a permission boundary; AI keys given to students are capped and revocable; the platform's own keys live in AWS Secrets Manager and are never put in a student's function.
- The installer and the teacher MCP never take passwords or API keys through an editor, never delete the stack, and refuse any update that would replace a data-holding resource.
- Releases are built by CI from a tag, with build provenance and published checksums (`sha256sums.txt`).

Details: `docs/TECHNICAL_SPECIFICATION.md` (security model) and `docs/AWS_LEARNINGS.md`.

## Supported versions

Only the newest release receives fixes. Teachers update with `plan` and `apply` (see the teacher guide); the student kit then follows automatically. This project is a **public beta** and has had **no third-party security review**.

## For teachers

Use an IAM user with MFA for the install, delete its access key afterwards, keep the AWS Budget on, and apply releases promptly (`status` tells you when one is out).
