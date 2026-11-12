  function createPetActions(CFG, isCurrent) {
    // Official 1.0.2 calls these actions separately from pet animations. Leave
    // hydration, spending, disk persistence and account switching untouched.
    var clickStore = null;
    var clickActions = [];
    var socketSend = null;
    var socketPrototype = null;
    var pausedSend = null;
    var clickPause = { counting: false, reporting: false, blockedReports: 0 };

    // Reuse EmotionMenu's selection event: the native handler owns local bubbles,
    // room delivery and telemetry. Never manufacture room socket messages.
    var autoEmote = { sent: 0, available: false, limited: 0 };
    var emoteTimes = [];
    var EMOTES_PER_SECOND = 20;
    function emoteBudget() {
      var value = Math.round(Number(CFG.auto_emote_rate));
      return isFinite(value) ? Math.max(1, Math.min(20, value)) : EMOTES_PER_SECOND;
    }
    function sendRandomEmote() {
      if (!CFG.auto_emote || !isCurrent()) return;
      // A rolling window avoids a burst of 40 at a fixed second boundary.
      // Keep this budget across switch toggles; excess taps are never queued.
      var now = typeof performance !== 'undefined' ? performance.now() : Date.now();
      while (emoteTimes.length && now - emoteTimes[0] >= 1000) emoteTimes.shift();
      if (emoteTimes.length >= emoteBudget()) { autoEmote.limited++; return; }
      try {
        var root = document.querySelector('#app');
        var queue = root && root._vnode ? [root._vnode] : [];
        var seen = new Set();
        var menu = null;
        for (var i = 0; i < queue.length && i < 512; i++) {
          var vnode = queue[i];
          if (!vnode || typeof vnode !== 'object' || seen.has(vnode)) continue;
          seen.add(vnode);
          var component = vnode.component;
          if (component) {
            if (component.type && component.type.__name === 'EmotionMenu' && !component.isUnmounted) {
              menu = component; break;
            }
            if (component.subTree) queue.push(component.subTree);
          }
          if (Array.isArray(vnode.children)) queue.push.apply(queue, vnode.children);
          if (vnode.suspense && vnode.suspense.activeBranch) queue.push(vnode.suspense.activeBranch);
        }
        var props = menu && menu.props;
        var items = props && Array.isArray(props.emoteItems) ? props.emoteItems.filter(function (item) {
          return item && Number.isSafeInteger(item.id) && item.id > 0 && typeof item.name === 'string' && item.name.length > 0;
        }) : [];
        autoEmote.available = !!(items.length && typeof props.assetUrl === 'function' && typeof menu.emit === 'function');
        if (!autoEmote.available) return;
        var item = items[Math.floor(Math.random() * items.length)];
        var src = props.assetUrl('emote/' + item.name);
        if (typeof src !== 'string' || !src) return;
        emoteTimes.push(now);
        menu.emit('select', { id: item.id, src: src });
        autoEmote.sent++;
      } catch (e) {
        // A changed native component must never interrupt the original tap.
        autoEmote.available = false;
      }
    }

    function restoreClickActions() {
      clickActions.forEach(function (record) {
        if (clickStore[record.name] === record.wrapper) clickStore[record.name] = record.original;
      });
      clickActions = [];
      clickStore = null;
      clickPause.counting = false;
    }

    function syncClickPause() {
      var store = null;
      try {
        var app = document.querySelector('#app');
        store = app && app.__vue_app__.config.globalProperties.$pinia._s.get('GlobalState');
      } catch (e) {}
      if ((!CFG.pause_clicks && !CFG.auto_emote) || store !== clickStore) restoreClickActions();
      if ((CFG.pause_clicks || CFG.auto_emote) && store && !clickStore) {
        clickStore = store;
        ['incrementLocalClickCount', 'addLocalClickCount'].forEach(function (name) {
          var original = store[name];
          if (typeof original !== 'function') return;
          var wrapper = function () {
            if (!isCurrent()) return original.apply(this, arguments);
            var result;
            if (!CFG.pause_clicks) result = original.apply(this, arguments);
            if (name === 'incrementLocalClickCount') sendRandomEmote();
            return result;
          };
          store[name] = wrapper;
          clickActions.push({ name: name, original: original, wrapper: wrapper });
        });
        clickPause.counting = CFG.pause_clicks && clickActions.length === 2;
      }
      clickPause.counting = CFG.pause_clicks && clickActions.length === 2;
      if (!CFG.pause_clicks) {
        restoreClickTransport();
        return;
      }
      if (pausedSend || !window.WebSocket || !window.MessageEvent) return;
      socketPrototype = window.WebSocket.prototype;
      socketSend = socketPrototype.send;
      var originalSend = socketSend;
      pausedSend = function (data) {
        // Only the native default-namespace Socket.IO `cr` event is a click
        // report. Never filter room traffic, heartbeat, HTTP logout or chests.
        var match = typeof data === 'string' && /^42([0-9]*)(\[.*\])$/.exec(data);
        var packet = null;
        try {
          var url = new URL(this.url);
          if (match && /\/socket\.io\/?$/.test(url.pathname)) packet = JSON.parse(match[2]);
        } catch (e) {}
        if (isCurrent() && CFG.pause_clicks && Array.isArray(packet) &&
            packet.length === 2 && packet[0] === 'cr' && packet[1] &&
            typeof packet[1].num === 'number' && this.readyState === 1) {
          clickPause.blockedReports++;
          if (match[1]) {
            // Complete the local callback with a non-success (no-code) result.
            // Nothing is sent to the server. This releases the Socket.IO ack
            // callback and lets the native 30-second timer re-arm, without
            // applying report success or clearing pending local clicks.
            var socket = this;
            var ack = '43' + match[1] + '[null]';
            setTimeout(function () {
              if (socket.readyState === 1) socket.dispatchEvent(new window.MessageEvent('message', { data: ack }));
            }, 0);
          }
          return;
        }
        return originalSend.apply(this, arguments);
      };
      socketPrototype.send = pausedSend;
      clickPause.reporting = true;
    }

    function restoreClickTransport() {
      if (socketPrototype && socketPrototype.send === pausedSend) socketPrototype.send = socketSend;
      // Keep the original reference alive for a wrapper retained by other code.
      pausedSend = null;
      clickPause.reporting = false;
    }

    return { sync: syncClickPause, clickPause: clickPause, autoEmote: autoEmote, dispose: function () {
      restoreClickActions();
      restoreClickTransport();
    } };
  }
