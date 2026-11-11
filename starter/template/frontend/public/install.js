/*
 * "Install this app" button and home-screen help. Plain JavaScript on purpose: it works with any frontend and does not touch your React code.
 *
 *  - Android, Chrome, Edge and desktop: when the browser offers an install, an "Install app" button is shown; a tap installs the icon.
 *  - iPhone and iPad (Safari has no install button): a "Add to Home Screen" button opens three steps.
 *  - Already installed (opened from the icon): nothing is shown.
 *
 * It caches nothing and asks for no permission. Remove the <script src="/install.js"> line in index.html to turn it off.
 */
(function () {
  if (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) return;
  if (window.navigator.standalone === true) return; // iPhone: already opened from the home screen

  var deferred = null;
  var isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;

  var style = document.createElement('style');
  style.textContent =
    '.install-bar{position:fixed;right:12px;bottom:12px;z-index:9999;font:14px system-ui,sans-serif}' +
    '.install-bar button{background:#2563eb;color:#fff;border:0;border-radius:999px;padding:10px 16px;font:inherit;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.2)}' +
    '.install-bar button:focus-visible{outline:3px solid #fbbf24;outline-offset:2px}' +
    '.install-panel{margin-top:8px;max-width:280px;background:#fff;color:#111;border:1px solid #d1d5db;border-radius:12px;padding:12px 14px;box-shadow:0 2px 12px rgba(0,0,0,.2)}' +
    '.install-panel ol{margin:6px 0 0 18px;padding:0}';
  document.head.appendChild(style);

  var bar = document.createElement('div');
  bar.className = 'install-bar';
  bar.hidden = true;
  var button = document.createElement('button');
  button.type = 'button';
  button.setAttribute('aria-label', 'Install this app on your device');
  bar.appendChild(button);
  document.body.appendChild(bar);

  var panel = null;
  function showIOSSteps() {
    if (panel) { panel.hidden = !panel.hidden; return; }
    panel = document.createElement('div');
    panel.className = 'install-panel';
    var title = document.createElement('strong');
    title.textContent = 'Add this app to your home screen';
    var steps = document.createElement('ol');
    ['Tap the Share button at the bottom of Safari (the square with an arrow).', 'Scroll down and tap "Add to Home Screen".', 'Tap "Add". The icon opens the app full screen.'].forEach(function (text) {
      var li = document.createElement('li');
      li.textContent = text;
      steps.appendChild(li);
    });
    panel.appendChild(title);
    panel.appendChild(steps);
    bar.appendChild(panel);
  }

  if (isIOS) {
    button.textContent = 'Add to Home Screen';
    button.addEventListener('click', showIOSSteps);
    bar.hidden = false;
    return;
  }

  // Android, Chrome, Edge and desktop: the browser offers the install once the page is ready
  window.addEventListener('beforeinstallprompt', function (event) {
    event.preventDefault();
    deferred = event;
    button.textContent = 'Install app';
    bar.hidden = false;
  });
  button.addEventListener('click', function () {
    if (!deferred) return;
    deferred.prompt();
    deferred.userChoice.finally(function () {
      deferred = null;
      bar.hidden = true;
    });
  });
  window.addEventListener('appinstalled', function () {
    deferred = null;
    bar.hidden = true;
  });
})();
