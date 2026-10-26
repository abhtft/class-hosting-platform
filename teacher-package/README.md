# class-platform-teacher

Install and update your own copy of the class hosting platform in your own AWS account with one command. You need Node 20+, the AWS CLI signed in (`aws configure`) and an AWS account.

```bash
npx -y class-platform-teacher install      # guided: AWS account, optional domain, features, your teacher login
npx -y class-platform-teacher@<new> upgrade   # when a new version is out: shows the change, asks, applies
npx -y class-platform-teacher editor       # add the teacher tools to VS Code / Antigravity
```

Add `--dry-run` to `install` or `upgrade` to see every AWS command without running any. Full guide: `docs/TEACHER_SETUP.md` in this package. What changed in each version: `CHANGELOG.md`.
