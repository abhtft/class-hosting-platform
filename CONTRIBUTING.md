# Contributing

Thank you for helping. This is a small project with one rule that matters: **teachers must be able to update safely**, so changes are additive, new features ship **off by default**, and nothing replaces a data-holding resource.

## Run the tests

```bash
node tests/run.mjs          # the whole suite; needs Node 20+ and Python 3.12 with boto3; no AWS account
```

Everything runs against in-memory fakes (and a fake AWS CLI for the installer). `python tests/live_smoke.py` and a real install are optional checks against an AWS account of your own.

## Before you open a pull request

1. One change per pull request, with a test. New behaviour that touches `template.yaml` needs a guard test (see `tests/test_template_*.py`).
2. New CloudFormation parameters must have a safe default that changes nothing.
3. Update the documentation that describes it (`docs/TECHNICAL_SPECIFICATION.md` facts are checked by `tests/test_spec.py`) and add a line to `CHANGELOG.md`.
4. Never commit keys, account ids, domains or email addresses; `tests/test_no_identifiers.py` and the secret scan will fail the build.

## How releases are made (maintainers)

Bump `VERSION` and `CHANGELOG.md`, merge, then push a tag `v<version>`. The Release workflow tests, builds the teacher package and publishes the GitHub Release.
