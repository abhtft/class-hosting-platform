LINKEDIN LAUNCH KIT: Class Hosting Platform
==========================================
Files to upload (in D:\Github_Codes\class-hosting-platform-github\.temp\launch\out\):
  class-hosting-platform-carousel.pdf   -> the post itself, as a "document" (swipeable)
  class-hosting-platform-teaser.mp4     -> optional: a second post a few days later, or reply in comments
  class-hosting-platform-walkthrough.mp4 -> optional: first comment or the repository's README

POST A: THE LAUNCH (carousel)
-----------------------------
Document title (LinkedIn asks for one):
  Class Hosting Platform: every student publishes their own website

Post text (paste, then swipe the PDF under it):

  I built a platform for my own class and I'm open-sourcing it today.

  The problem: students build websites with AI editors, but getting them online means accounts, servers, keys, and a teacher babysitting all of it.

  The fix: one command installs a platform in YOUR OWN AWS account. Each student gets a token (not AWS access) and publishes from their AI editor, to their own address or their own domain.

  What I care about as a teacher:
  • I decide, per student, what's switched on: a small backend, class AI with a dollar cap, storage, a custom domain. Everything starts off.
  • An idle platform costs close to nothing, and AI spend is capped per student.
  • Updates are a plan I review, then apply. Backup and a clean "destroy" are built in.
  • Open source (MIT), and releases are signed.

  Honest status: public beta. It has been tested end to end on real AWS accounts, but mostly by me, which is exactly why I'm posting. If you teach or train and have an AWS account, I'd love 30 minutes of your time: run the install and tell me where you got stuck.

  Swipe for how it works. Repo and guide in the first comment.

  #opensource #edtech #aws #teaching #buildinpublic

FIRST COMMENT (post it yourself right after publishing, so the link is not in the post body):

  GitHub: https://github.com/abhtft/class-hosting-platform
  Teacher guide: https://github.com/abhtft/class-hosting-platform/blob/main/docs/TEACHER_SETUP.md
  Tried the install? Tell me what worked and what stopped you: https://github.com/abhtft/class-hosting-platform/issues/new?template=tried_the_install.md

ALT TEXT (add to the document if LinkedIn offers it):
  Eight slides. 1: Every student publishes their own website in one command, in your own AWS account. 2: The problem: accounts and servers, keys to hand out, a teacher babysitting it all. 3: How it works: teacher, your AWS account, students who get a token and never AWS access. 4: Creating a student token in the teacher page. 5: Per-student switches for backend, class AI, storage and custom domain, all off by default. 6: A student signs in, tells their AI editor to set up and publish, and the site is live. 7: Safe by design, and an honest public-beta status. 8: Testers wanted, with the repository address.

POST B: THE TEASER (a few days later, or if you prefer a video first)
-------------------------------------------------------------------
Upload class-hosting-platform-teaser.mp4 natively (not a link). Text:

  20 seconds: how a teacher gives every student their own website, without handing out AWS accounts.

  It's open source and in public beta. I'm looking for a few teachers to try the install and tell me honestly where it breaks. Link in the first comment.

  #opensource #edtech #aws

REPLIES TO HAVE READY (be honest, keep them short)
-------------------------------------------------
Cost?              An idle platform costs close to nothing. The drivers are student traffic, AI use (capped per student) and stored data. Add an AWS budget alert.
Is it safe?        Students never get AWS access; features are off by default with caps. It is a beta with no third-party security review; vulnerabilities go through private reporting.
Student data?      It stays in your AWS account. If you switch class AI on, prompts go to the provider you choose.
Why AWS only?      It is what I built and proved first. Other clouds are not planned yet.
Can you host it?   Not yet. Would that be useful? Tell me what you would need.
License?           MIT.

BEFORE YOU PRESS POST
---------------------
[ ] Private vulnerability reporting is switched on in the repository settings
[ ] Branch protection on main, secret scanning and Dependabot alerts are on
[ ] You ran the clean-room install test from the README once
[ ] You are free for the next 2 hours to answer every comment
[ ] Best days: Tuesday to Thursday, morning in your audience's time zone
[ ] Post from your personal profile, then reshare to the page
