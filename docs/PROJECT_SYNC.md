# Project sync

Students keep the **source files** of their project on the platform, so the same code is on every computer they use and a team can share one project **without GitHub accounts**. It is not Git: it moves whole files and stops when two people changed the same file.

## For a student

```bash
npm run push                       # send the files you changed (new, changed, deleted)
npm run pull                       # get what changed elsewhere (another computer, a teammate)
npm run project                    # your projects and what is not sent yet
npm run pull -- --project robot-club   # work on a team project (remembered for the folder)
```

- **New computer:** download the starter kit from the portal, `npm run login`, `npm run pull`. The kit's own files come from the portal; the project's files from the platform.
- **Conflict:** `push` stops and says who changed the file; nothing is overwritten. `pull` saves their copy as `<file>.theirs`. Keep what you want in your file, delete the `.theirs` copy, then `npm run push -- --mine`. Or `npm run pull -- --theirs` takes the platform's copy (yours is kept as `<file>.mine`).
- **Never synced:** the kit's files, `node_modules`, `.git`, build output, `.env` files, keys, the `knowledge/` books, and what you list in `.syncignore` (one name, `folder/` or `*.ext` per line). Limits: 5 MB a file, 50 MB a project, 500 files.

## Automatic saving (ask once, then every deploy)

A student is asked **once** per project folder: "Save a copy of your project files on the platform, now and after every deploy?" In a terminal `npm run deploy` asks it after the first deploy; in an AI editor the `status` tool tells the AI to ask, and the student's yes makes the AI call `push_project`. A yes copies the whole project (the first save) and turns on **saving after every deploy** (a failed save is a warning and never stops the deploy). A no is remembered and never asked again. The answer lives in `.class-project.json` next to the project; `npm run project -- --auto off` or `on` (the editor tool `project_auto`) changes it. `npm run deploy -- --no-project-sync` skips it once. A conflict with a teammate's change is a warning: the deploy still happens and nothing is overwritten.

## Moving existing users and existing work onto project sync

Nothing is lost and nothing changes for a student until they use the new commands. The steps, in order:

**Teacher (once)**
1. Update the platform: `plan`, show the plan, `apply <plan id>` (this release adds one permission, `projects/*`, and republishes the portal, the kit and the editor-tools package). Check `verify`.
2. Tell students that you can read the project files they push, and what they are for.
3. Optional: in the **Projects** tab make a team project for each group and add its members.

**A student who already has a project (kit folder)**
1. Open a terminal in the project and run any kit command, for example `npm run deploy` or `npm run project`. The kit **updates itself first** (old copies of changed kit files go to `.kit-backup/`), which adds `npm run push`, `npm run pull` and `npm run project`. Their own code is never touched.
2. The next `npm run deploy` asks once whether to save the project on the platform. A yes copies the whole existing project (about 50 files for the starter) and keeps it saved after every deploy. (`npm run push -- --dry-run` shows what would be sent; `npm run push` does the first copy by hand.)
3. A big file or folder that should not be shared: add its name to `.syncignore` and push again.

**The same student on another computer**
1. Download the starter kit from the portal and unzip it, `npm run login` (the token is entered by the student, hidden), then `npm run pull`.
2. From then on: `npm run pull` when they sit down, `npm run push` when they finish.

**A team**
1. The teacher makes the team project and adds the members (**Projects** tab).
2. One member pushes the existing code: `npm run push -- --project <team-id>` (from their folder). The others: new folder from the kit, `npm run login`, `npm run pull -- --project <team-id>`.
3. Everyone then pulls before they start and pushes when they finish. If `push` reports a conflict, follow the message: `pull`, keep what you want, delete the `.theirs` copy, `push --mine`.

**Students who work through an AI editor (the tool server)**
- *Kit folder with the editor's own settings (VS Code `.vscode/mcp.json`, which starts `tools/mcp.mjs`):* the kit update in step 1 above also updates the tool server's file. **Restart the tool server (or reload the editor)** and the tools `project_status`, `push_project`, `pull_project` and `project_auto` appear. The AI's first `status` of the session says the project is not saved yet; the AI asks the student once, and on a yes pushes the whole project. From then on every deploy saves it by itself.
- *Package set up earlier with `npx -y <portal>/class-platform-mcp.tgz setup`:* the editor is pinned to an older version on purpose, so it keeps working but has no project tools. Ask the AI to run `status`: it says a newer tool package exists. Run `npx -y <portal>/class-platform-mcp.tgz setup` **once**, accept the replacement of the `class-platform` entry, and restart the tool server. The project must also have the new kit (step 1) because the tools run the project's own `portal-deploy.mjs`.
- Either way the student never gives the AI the token; `npm run login` stays the student's own step.

## For the teacher

The **Projects** tab lists every project (personal and team), the files with who last changed them, and the history (who, when, which files). You can **make a team project and choose its members**, open any file, **put an earlier version of a file back**, **freeze** a project (nothing can be pushed), **clean up old versions** and **delete** a project. A student who leaves a team can no longer pull or push; their earlier changes stay. Nothing needs switching on, and nothing runs or costs while idle. The files are in the platform's own bucket (`projects/`), and the cost is the storage (cents). The teacher can read students' source files: tell students.

## How it works

- **Storage.** A file is stored by its SHA-256 (`projects/<p|t>/<name>/blobs/<sha256>`): uploaded once, never overwritten. One item in the tokens table is the project's **manifest** (path to sha256, size, author, time) with a **revision** number; one small history item per commit (kept 180 days).
- **Push** is two calls. `prepare` checks the changes against the project as it is now and returns S3 upload forms for the files S3 does not have yet (S3 itself enforces the size and the SHA-256). `commit` checks that the files arrived and replaces the manifest **only if nobody committed in between** (a conditional write on the revision). Every change says which version of the file it started from (`base`); if somebody else changed that path since, the whole push is refused with `409 conflict` and the list of paths and who changed them. Changes to other paths never conflict, even from an older revision. A project that keeps changing under a push answers "busy" after three tries.
- **Pull** lists the manifest and downloads what differs through short-lived links; the client checks every file's SHA-256 before writing it. A conflicting file is never overwritten.
- **Safety.** Paths are relative, without `..`, backslashes or control characters, and never `.env*`, keys, `.git` or `node_modules` (the server refuses them too). A student reaches only their personal project and the teams they belong to; the portal never reads a file's bytes (it signs links); S3 access is the portal function only (`projects/*`), not CloudFront.
- **Limits.** 500 files, 5 MB a file, 50 MB live per project, 150 MB of stored versions in total (old versions stay until the teacher cleans up), 100 paths a push.
- **Backups.** `export` copies `projects/` and the manifests (they are in the tokens table).

## Decisions and what is left out (version 2.3.2)

- A personal project is made on the first push; a team project only by the teacher. One token per person (no shared token), so history shows who changed what.
- No line-by-line merging (whole files only), no branches, no per-file locks, no deleting a student's personal project when the student is removed (delete it in the Projects tab).
- Not synced on purpose: the kit's files (they follow the teacher's portal), `platform.json`, secrets.

## Routes

Student (login session): `GET /api/student/projects`; `GET /api/student/projects/{id}` (`personal` or a team id); `POST .../{id}/download` `{paths}`; `POST .../{id}/prepare` and `POST .../{id}/commit` `{changes: [{path, op: put|delete, sha256, size, base}]}`.
Teacher: `GET|POST /api/admin/projects`; `GET|POST|DELETE /api/admin/projects/{p|t}/{name}`; `POST .../file` `{path}` (a download link), `.../restore` `{path, rev}`, `.../compact`. See [API_REFERENCE.md](API_REFERENCE.md) section 7.
