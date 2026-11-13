# Design: signing the student kit (F1, part 2)

Status: proposal, nothing coded. Private (.temp is git-ignored). Needs 3 decisions from the owner (section 9).

## 1. What problem is left after the IAM fix

Since 5685e7b no platform function can write `portal/`. Today only these can change what students download:
the teacher's own credentials (`portal-publish`), CloudFormation, and any AWS admin in the account.

A student's computer still trusts whatever `https://<portal>/kit-version.json` and `starter-kit.zip` say. The hash inside `kit-version.json` proves the
zip matches the JSON, not that the JSON is the teacher's. So whoever can write `portal/` once can run code on every student's computer on their next
`npm run deploy` (kit auto-sync).

Signing closes that: the files are accepted only if signed with a key that is **not** an S3 permission.

Honest value: this is defence in depth. It stops (a) a stolen or leaked credential that can write S3 but cannot sign, (b) a future bug or a bucket-policy
mistake that opens `portal/` again, (c) a rogue or compromised admin tool that only uploads. It does NOT stop someone with full admin credentials
(they can also call KMS) or someone who steals the teacher's machine.

## 2. What is covered, what is not

| Channel | Run by | Covered by this design |
|---|---|---|
| `starter-kit.zip` + `kit-version.json` (kit auto-sync: `portal-deploy.mjs`, `tools/*`, rules) | the student's computer, every deploy | **Yes** |
| `class-platform-mcp-<v>.tgz` (editor tools, started by `npx`) | the student's editor | **Not yet.** `npx` cannot verify a signature on a URL package. Phase 2: `setup` verifies the tarball against a signed `mcp-version.json` before writing the editor setting, and the setting keeps the exact pinned URL |
| Portal pages (`web/*.js`) | browsers, including the teacher's login page | **No.** Browsers cannot verify signatures; this is why the IAM separation (done) matters most |

## 3. The scheme

- One **signing key per platform**. ECDSA P-256 + SHA-256 (what AWS KMS offers for signing; Node verifies it with `node:crypto` and no dependency).
- Publish writes, next to `kit-version.json`:
  - `kit-version.json` gains `serial` (publish time in seconds; see 5) and `keyId` (fingerprint of the public key).
  - `kit-version.json.sig`: base64 signature over the exact bytes of `kit-version.json` (no re-serialising, so no canonical-JSON pitfalls).
  - `signing-key.pub`: the public key (SPKI PEM), informational and for first pinning.
- `kit-version.json` already carries `zipSha256` and the `managed` hashes, so one signature covers the zip, the file list and every hash.
- **Verification (student side) lives in `tools/platform.mjs`** (`update`, `sync`) and nowhere else:
  1. download `kit-version.json` and `.sig`, verify with the **pinned** public key;
  2. only then trust `zipSha256` and the zip;
  3. refuse if `serial` is lower than the installed one (replay of an old signed kit);
  4. once a project has a pin, an unsigned manifest is refused (otherwise an attacker just deletes the `.sig`).
- `portal-deploy.mjs` `syncKit` only decides *whether* to call `platform.mjs sync`; it never executes downloaded content itself, so it needs no change
  other than the failure behaviour in 6.

## 4. Where the key lives (decision 1)

| | A. AWS KMS key in the platform stack (recommended) | B. Key file on the teacher's computer |
|---|---|---|
| Secret on disk | none: the private key never leaves KMS | yes (`~/.class-platform-teacher/signing.key`), one more secret to protect and back up |
| Lost laptop / second computer | nothing lost; any computer with the AWS profile can publish | all students must re-pin; the key must be copied between computers |
| Cost | about $1 per month + cents (not free) | free |
| Created/removed by | `template.yaml` (key + alias); `destroy` removes it (KMS keeps it 7-30 days pending deletion) | `install` creates, `destroy` deletes the file |
| Who can sign | principals with `kms:Sign` on this key: the teacher's profile. Deploy, Sweeper, Gateway, sandbox roles get **nothing** (a template test asserts it) | whoever can read the file |
| `transfer` to a new account | new key, so students must re-pin (see 7) | key can move, but the file must be carried |

Recommendation: **A**. It matches the existing rule that secrets do not sit in files, needs no backup story, and fits "the teacher's own credentials publish".
The $1 per month is the only cost; say so in the docs. (Free option B is fine if the owner prefers zero extra cost; the verification side is identical.)

## 5. Replay and teacher rollback

The teacher can roll the kit back (an older kit is published on purpose). A plain "no older version" rule would block that. So the signed manifest has
`serial` = publish time; a rollback publishes the old *content* with a *new* serial, and clients reject only a lower serial. An attacker replaying a
genuinely old signed manifest is rejected; the teacher's rollback works.

## 6. First pin, and what students see

- **First pin (trust on first use).** The kit zip a student first receives (Start-here card / `init_project`) carries `signingKey` (the public key) in
  `platform.json`. That first download is trusted because of the portal address and HTTPS, as today. It protects every later update and every existing
  project; it cannot protect a student who first downloads from an already-tampered portal. Optional hardening: the Start-here page and the teacher's
  welcome message show the key fingerprint in groups of four characters, and `tools/platform.mjs status` prints it, so a careful student or the
  teacher can compare. Documented, not required.
- **Existing 2.0.x projects.** They have no pin. The first signed kit they sync is accepted the old way and installs the pin; from then on it is enforced.
- **A signature failure never blocks a student's work.** `sync` does not apply the update, prints one clear message ("The kit from your portal is not signed
  by the key this project trusts. Nothing was changed. Tell your teacher."), and the deploy continues with the current kit. Exception: the portal
  requires a newer minimum kit; then the deploy stops with the same message (so a forged "minimum" cannot be used to starve students silently).
- The teacher gets the signal from `verify` and `status` (below) and from students' reports; there is no phone-home.

## 7. Key change: transfer, loss, rotation

- Only happens on `transfer`/rebuild into a new account (new KMS key) or a deliberate rotation.
- The new platform publishes the new key. Students run `npm run trust` (a command in `platform.mjs`): it shows the old and the new fingerprint, asks
  the student to confirm with the teacher, then re-pins. Until then they keep working with a warning (6).
- If the old platform is still up during a transfer, `transfer` publishes a **rollover note** signed by the old key naming the new key, so projects can
  re-pin by themselves (phase 2; start with the manual `trust`).

## 8. Changes by file

- `template.yaml`: `AWS::KMS::Key` (`KeySpec: ECC_NIST_P256`, `KeyUsage: SIGN_VERIFY`, policy: account root for administration, no principal besides it), an alias, an output `SigningKeyId`; **no** kms permission on any function role.
- `admin.mjs portal-publish` + `starter/publish-files.mjs`: take a `sign(bytes) -> signature` function; the real one calls `aws kms sign` (via the same `aws()` helper and profile); tests and the dev portal pass a local-key signer. Writes `serial`, `keyId`, `.sig`, `signing-key.pub`.
- `starter/build-kit.mjs`: put `signingKey` into the kit's `platform.json`.
- `starter/template/tools/platform.mjs`: `verifyManifest()`, the serial check, `trust`, fingerprint in `status`; `update-notice.mjs` unchanged (reads only the version).
- `student/portal-deploy.mjs`: failure behaviour in 6.
- `installer/verify.mjs` (the teacher's `verify`): fetch manifest + sig and verify with the key from the stack; fail loudly if missing or wrong.
- `installer/engine.mjs` `destroy`: nothing to add if the key is in the stack (CloudFormation schedules its deletion); mention it in the destroy message.
- Docs: `docs/TEACHER_SETUP.md` (what signing is, the $1, `trust`), `docs/STUDENT_KIT.md`, `docs/TECHNICAL_SPECIFICATION.md` (trust model), CHANGELOG.
- Kit changes => bump `kitVersion`, record in `tests/kit-versions.json`.

## 9. Decisions needed from you

1. **Key custody:** KMS (recommended, about $1 per month, no secret on disk) or a local key file (free, one more secret, harder on a second computer/transfer)?
2. **Failure behaviour:** continue with the current kit and warn (recommended), or stop the student's deploy until the teacher fixes it?
3. **Scope now:** kit only (recommended; the MCP package in phase 2), or both together (more work: `setup` changes and a pinned-URL migration)?

## 10. Tests

- Sign/verify round trip with a local P-256 key in the same DER form KMS returns.
- Reject: tampered `kit-version.json`; zip not matching `zipSha256`; wrong key; missing `.sig` after a pin; lower `serial`; unsigned manifest after a pin.
- Accept: teacher rollback (old content, new serial); first sync of a 2.0.x project installs the pin.
- Failure behaviour: nothing written, exit message as above, deploy continues; the minKit exception stops it.
- Template test: the KMS key exists with `SIGN_VERIFY`; no function role has any `kms:` action.
- Fake `aws kms sign` for `portal-publish`; `installer/verify` test for a bad signature.
- Real AWS once (throwaway profile): create the key, publish, verify from a clean project, tamper with the S3 object by hand, see the refusal, `destroy` and confirm the key is pending deletion.

## 11. Size and order

About 1.5 to 2 days including docs and the real-AWS pass. Release as 2.1.0 (a kit change; students receive it by auto-sync, teachers run `upgrade` to get
the key).

My view on order: the logs work first (daily student value), then this, because after the IAM fix this is protection against a second failure, not a
live hole. If a public launch is days away and you want the strongest story, do it before launch; the trust model is then simple to explain.
