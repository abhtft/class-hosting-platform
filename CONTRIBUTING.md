# Contributing

Thank you for helping. This is a small project with one rule that matters: **teachers must be able to update safely**, so changes are additive, new features ship **off by default**, and nothing replaces a data-holding resource.

## Have an idea? How to propose it

You do not need access to this repository or an AWS account. `main` only changes when a maintainer merges a reviewed pull request, so you can work freely on your own copy.

1. **Small fix or a docs improvement?** Go straight to a pull request. **Bigger idea?** Open an issue first (use the feature request template) so we can agree on the direction before you spend time on it.
2. **Get the code.** Fork the repository on GitHub (the Fork button), then clone *your* fork and make a branch. Always work on a branch, never on `main`:

   ```bash
   git clone https://github.com/<your-username>/class-hosting-platform.git
   cd class-hosting-platform
   git checkout -b my-idea
   ```

3. **Make the change and run the tests:** `node tests/run.mjs` (see below).
4. **Push the branch to your fork and open a pull request** against `main` of this repository. GitHub shows a "Compare & pull request" button after you push. Say what you changed and why.
5. **What happens next.** The automated checks run (for a first-time contributor a maintainer may need to approve that run). A maintainer reviews, may ask for changes, and merges when it is ready. Please be patient: this is a small, volunteer-run project.

Two things specific to this repository:

- **Do not change `VERSION` or the version numbers.** Maintainers do that when they cut a release.
- **If you change the student kit** (anything under `starter/`, or `student/portal-deploy.mjs`, the class AI or class storage helpers), the test "kit 2.x: its contents match the version recorded" will fail. That is expected: kit changes must be released with a new version number. Just say in the pull request that you changed the kit, and a maintainer will bump the version and record it before merging.

Teachers install from **releases**, not from `main`; `main` is where development happens. That is why changes are reviewed before they are merged.

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
