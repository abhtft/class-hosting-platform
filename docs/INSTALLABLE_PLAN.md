# Installable websites ("add to home screen"): design and status

> **Status: built and released in 2.6.0 (new projects) and 2.7.0 (older projects, as an add-on).** The real-device check and the optional offline support are still to do: see [FUTURE.md](FUTURE.md) section 8. The file name is kept because the test suite and the add-on catalogue refer to it.

## 1. What it is

A **Progressive Web App**: the same website, plus a few files that let a phone or computer install it as an icon. No store, no store account, no store review, no new AWS resource.

| Visitor | What they see | What happens |
|---|---|---|
| Android (Chrome, Edge, Samsung Internet) | An **Install app** button, shown when the browser offers the install | An icon is added; the app opens full screen |
| iPhone and iPad (Safari) | No install prompt is possible in Safari, so the page shows the three **Add to Home Screen** steps | The same icon is added; Safari gives less app behaviour |
| Computer (Chrome, Edge) | An install icon in the address bar and the same button | The site opens in its own window |

## 2. What a student's site gets

1. A **web app manifest** (`manifest.webmanifest`): name, colours, icons, "open full screen".
2. **Icons:** 512 by 512 and 192 by 192, each as "any" and "maskable", plus an iPhone icon. The kit ships plain defaults; the student may replace them.
3. **An install button and a small script** (`public/install.js`, about 60 lines). It shows the button only when it can be used, and on iPhone shows the steps instead. Six lines are added to `index.html`.

New projects get all of this from the starter (2.6.0). Older projects get it through the **Install as an app** add-on (2.7.0): the student's AI editor asks first, and `remove_addon` puts the page back exactly as it was.

## 3. Rules that keep it safe

- **Nothing private is cached.** The install script caches nothing; there is no service worker yet. Nothing from the backend or any token is stored.
- **HTTPS only.** The platform's addresses are secure already.
- **No new permission.** Installing gives the site no notifications, camera or location.
- **The visitor decides.** Nothing is installed without a tap.
- **One source.** The files come from the kit, so a teacher's update reaches students through the normal kit sync; the student's own edits to icon, name and colours are kept.

## 4. Cost

No store fees, no new hosting, no new AWS resource. The files are read once per visitor.

## 5. Limits to tell students and teachers

- iPhone is limited: Safari has no install button and less app behaviour (less storage, no background work). The icon still opens the site.
- Without the optional offline support, the icon opens the site only when there is a connection.
- Nobody finds the site through a store; people visit the address and install from there.
- A new version of the site reaches the phone the next time the page is opened online.
- Android may show the icon in a round or square mask; the "maskable" icon keeps the important part in the centre.

## 6. Remaining

| Item | Status |
|---|---|
| Real-device check on one Android phone and one iPhone | Not done (FUTURE.md section 8) |
| Optional offline support (a service worker that keeps only the app shell) | Not built; only if asked |
