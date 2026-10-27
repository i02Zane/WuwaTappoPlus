(function () {
  var VERSION = __VERSION__;
  if (window.__WUT_TOAST__ && window.__WUT_TOAST__.version === VERSION) return 'exists';
  if (window.__WUT_TOAST__) window.__WUT_TOAST__.uninstall();

  // The toast window is its own WebView: messages such as "宝箱冷却中" are
  // rendered here and never reach the pet page, so a refused click would
  // otherwise leave no trace of its own. Keep a small rolling buffer with
  // timestamps; the daemon drains it and writes one log line per message.
  var T = window.__WUT_TOAST__ = { version: VERSION, entries: [], last: '', installedAt: Date.now() };
  var MAX = 50;

  function read() {
    var root = document.querySelector('.toast-overlay-root');
    if (!root) return '';
    return (root.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 160);
  }

  function sample() {
    var text = read();
    if (text === T.last) return;
    T.last = text;
    // An empty reading is the toast being dismissed, not a new message. The
    // same text can then legitimately appear again as a second toast.
    if (!text) return;
    T.entries.push({ t: Date.now(), m: text });
    if (T.entries.length > MAX) T.entries.shift();
  }

  var observer = new MutationObserver(sample);
  observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  // The observer covers DOM changes; the timer is a cheap safety net for a
  // toast revealed by a pure CSS transition.
  var timer = setInterval(sample, 250);

  T.sample = sample;
  T.uninstall = function () {
    observer.disconnect();
    clearInterval(timer);
    delete window.__WUT_TOAST__;
  };

  sample();
  return 'installed v' + VERSION;
})()
