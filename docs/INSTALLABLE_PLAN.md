# Installable website ("add to home screen"), plan

> **Status: step I1 is built (2026-11-11, in the working tree, not committed or released as 2.6.0).** Built: the manifest, three default PNG icons, the install script `frontend/public/install.js` and the links in the starter's `index.html`; the test `tests/test_installable.mjs` (step 50 of the suite) checks them, and the build of the starter site carries them into `dist/`. Owner's answers: install by default in new projects; older projects later through the AI editor (step I3); no offline support yet (I4 later). **Not yet done:** the real-device check on an Android phone and an iPhone (I2) and the add-on for older projects (I3). Request from the owner (2026-11-11): a **download / install button on the website**, so a visitor on a phone gets a small icon that opens the site like an app. No app store, no store account, no store review. Low cost.

## 1. What this is (and is not)

This is a **Progressive Web App (PWA)**: the same website, plus a small set of files that let the phone or computer install it as an icon. No separate app is built, and nothing is published to Apple's or Google's stores.

| Visitor | What they see | What happens |
|---|---|---|
| **Android (Chrome, Edge, Samsung Internet)** | An **Install** button on the page | Tapping it adds an icon to the home screen and the app drawer. It opens full screen, without the browser bar |
| **iPhone / iPad (Safari)** | No install prompt is possible in Safari, so the page shows a short picture guide: "Share, then Add to Home Screen" | The same icon is added. Safari gives less of the app behaviour than Android (see section 6) |
| **Computer (Chrome, Edge)** | An install icon in the address bar, and the same Install button | It opens in its own window |

Your understanding is right. The only difference from a store app is that there is no store listing, so people must visit the site first. That is what you wanted.

## 2. What the student's site gets

Three small additions to the **starter site** (the kit), so every new project has them:

1. **A web app manifest** (`manifest.webmanifest`): the name, colours, the icon and "open full screen". A text file of about 1 KB.
2. **Icons**: one large icon (512 by 512) and one small (192 by 192), with an "any" and a "maskable" version. The kit ships a plain default icon that the student can replace with their own.
3. **An install button and a small script** in the site: it catches the browser's install offer on Android and desktop, shows the Install button only when it can be used, and on iPhone shows the Add to Home Screen steps instead. About 60 lines of code, in the kit's frontend.

Optional, later: a **service worker** (a small script that lets the site open when the network is slow or briefly off). See section 6 for why it is not in phase 1.

## 3. Cost

| Item | Cost |
|---|---|
| Store accounts (Apple, Google) | **None**: not used |
| Extra hosting | **None**: the files are served with the site on the platform that already exists |
| Extra AWS resources | **None** (no new function, table or bucket) |
| Build and tests | Your existing toolchain. No new paid service |
| Monthly running cost | Effectively zero: a 1 KB text file and a few icons, read by each visitor once |

## 4. Rules that keep it safe and simple

- **Nothing private is cached.** The install script caches only the app shell (the page, its code, its icons). It never stores a student's data, a token, or an answer from the backend, so a shared phone cannot show another person's data.
- **HTTPS only.** Installation is allowed only on the platform's secure address (it already is).
- **No install by default for a school's data.** Installing never gives the site any new permission (no notifications, no camera, no location) unless the student adds one.
- **The visitor decides.** The button only offers installing; nothing is installed without a tap.
- **One source.** The three files come from the kit, so a teacher's update reaches every student through the usual kit sync, and the student may edit the icon, the name and the colours.

## 5. Where it goes in the build order

| Step | What | Done when |
|---|---|---|
| **I0. Decide** | Owner agrees: starter gets it by default (recommended), and the install button text | Owner's yes |
| **I1. Starter files** | Manifest, two default icons, the install script and button, the iPhone guide, all in the kit | A new project builds; the manifest and icons are served; a test checks the files exist and the manifest is valid |
| **I2. Real-device check** | Install on one Android phone and on one iPhone from `test_claude`'s site | The icon opens the site full screen; a short checklist recorded |
| **I3. Existing projects** | A guide for the AI editor and an add-on entry ("make my site installable"): the AI adds the same files to an older project and asks first | The add-on adds the files and the build passes |
| **I4. Optional service worker** | Offline shell (see section 6) | Only if the owner asks after I1 to I3 |

Recommended order: after the settings work, which is done, and before RAG quality, because it is small and visible to pilot teachers.

## 6. Limits to tell students and teachers

- **iPhone is limited:** Safari has no install button and gives less app behaviour (for example less storage and no background work). The Add to Home Screen icon still works and opens the site.
- **Offline use** needs the optional service worker (I4). Without it the icon opens the site only when there is a connection.
- **No store presence:** people find the site by its address, not by searching a store. Tell them the address and how to install it.
- **Updates:** a new version of the site reaches the phone the next time the page is opened online. There is no store update prompt.
- **Icon look:** Android may show the icon in a round or square mask. The "maskable" icon keeps the important part in the centre.

## 7. Decisions for the owner

1. **Is this the right understanding** (a web-app install icon, no store)? Yes / no.
2. **Default for new projects:** include it in the starter (recommended, costs nothing and makes it the norm), or only when a student asks?
3. **Existing projects (I3):** offer it through the AI editor as an add-on (recommended), or leave them to decide?
4. **Offline support (I4):** not now (recommended, keeps it simple and private), or later?
5. **App name and icon:** the platform's default name ("My class project") and icon are placeholders. Do you want a name and icon for your platform's own portal too?
