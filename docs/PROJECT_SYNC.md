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
