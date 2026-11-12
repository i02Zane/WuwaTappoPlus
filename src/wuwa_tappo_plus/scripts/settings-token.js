  function createTokenTools() {
    function cookieValue(name) {
      try {
        var parts = document.cookie ? document.cookie.split(';') : [];
        for (var i = 0; i < parts.length; i++) {
          var part = parts[i].trim();
          if (part.indexOf(name + '=') === 0) {
            return decodeURIComponent(part.slice(name.length + 1));
          }
        }
      } catch (e) {}
      return '';
    }

    function readInfoToken() {
      return cookieValue('kuro_infoToken');
    }

    function readTokenExpiry() {
      var raw = cookieValue('kuro_lastLoginTime');
      if (!raw) return null;
      var text = String(raw).trim();
      if (!text) return null;
      var value = Number(text);
      if (Number.isFinite(value) && value > 0) return value < 1e12 ? value * 1000 : value;
      var parsed = Date.parse(text);
      return Number.isFinite(parsed) ? parsed : null;
    }

    function formatTokenExpiry(expiry) {
      if (!expiry) return '有效期未知';
      var ms = expiry - Date.now();
      if (ms <= 0) return '已过期，请重新登录或续期';
      var days = Math.floor(ms / 86400000);
      var hours = Math.floor((ms % 86400000) / 3600000);
      var minutes = Math.floor((ms % 3600000) / 60000);
      return '剩余' + (days > 0 ? days + '天' : '') +
        (hours > 0 ? hours + '小时' : '') + minutes + '分钟';
    }

    function updateTokenExpiry() {
      var node = document.getElementById('wut-token-expiry');
      if (!node) return;
      var expiry = readTokenExpiry();
      node.textContent = '当前Token有效期：' + formatTokenExpiry(expiry);
      var soon = expiry === null || expiry - Date.now() < 2 * 86400000;
      node.dataset.soon = soon ? 'true' : 'false';
      var renew = document.getElementById('wut-token-renew');
      if (renew) {
        renew.hidden = false;
        renew.style.display = '';
      }
    }

    function isLoggedIn() {
      try {
        var root = document.querySelector('#app');
        var app = root && root.__vue_app__;
        var pinia = app && app.config.globalProperties.$pinia;
        var store = pinia && pinia._s.get('GlobalState');
        return store ? store.isLoggedIn === true : null;
      } catch (e) { return null; }
    }

    // Token login is a recovery path for a fresh or logged-out client. When the
    // app already has a session, pasting another token only risks clobbering it.
    function syncTokenLoginVisibility() {
      var row = document.getElementById('wut-token-login-row');
      if (!row) return;
      var hidden = isLoggedIn() === true;
      row.hidden = hidden;
      row.style.display = hidden ? 'none' : '';
    }

    function showAppToast(message, delay) {
      var script = document.querySelector('script[type="module"][src]');
      if (!script) {
        try { window.alert(message); } catch (e) {}
        return;
      }
      import(script.src).then(function (module) {
        var values = Object.values(module);
        var toast = values.find(function (value) {
          return typeof value === 'function' && value.name === 'showToast';
        });
        if (!toast) throw new Error('toast API unavailable');
        toast(message, delay || 2500);
      }).catch(function () {
        try { window.alert(message); } catch (e) {}
      });
    }

    function setCookieValue(name, value, days) {
      var expires = new Date(Date.now() + days * 86400000).toUTCString();
      document.cookie = name + '=' + encodeURIComponent(value) + '; expires=' + expires + '; path=/';
    }

    function renewToken() {
      var script = document.querySelector('script[type="module"][src]');
      if (!script) return Promise.reject(new Error('native module missing'));
      return import(script.src).then(function (module) {
        var values = Object.values(module);
        var renew = values.find(function (value) {
          return typeof value === 'function' && value.name === 'renewTokenIfNeeded';
        });
        if (!renew) throw new Error('renew API unavailable');
        var original = cookieValue('kuro_lastLoginTime');
        var temporary = String(Math.floor((Date.now() + 60 * 1000) / 1000));
        setCookieValue('kuro_lastLoginTime', temporary, 7);
        function restore() {
          if (original) {
            setCookieValue('kuro_lastLoginTime', original, 7);
          } else {
            document.cookie = 'kuro_lastLoginTime=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
          }
        }
        return renew().then(function (renewed) {
          if (!renewed) {
            restore();
            throw new Error('renew rejected');
          }
          return true;
        }, function (error) {
          restore();
          throw error;
        });
      });
    }

    function extractToken() {
      var token = readInfoToken();
      if (!token) {
        showAppToast('未找到 Token，请先登录 Wuwa Tappo。');
        return;
      }
      copyToken(token).then(function () {
        showAppToast('Token已提取至剪贴板。Token是你的账号在Wuwa Tappo的登录凭证，'
          + '请勿分享给他人，也不要粘贴到不可信的应用或工具。'
          + '如果泄露Token，请立刻在Wuwa Tappo退出账号', 8000);
      }).catch(function () {
        showAppToast('复制失败，请检查剪贴板权限后重试。');
      });
    }

    function applyAuthToken(token) {
      var script = document.querySelector('script[type="module"][src]');
      if (!script) return Promise.reject(new Error('native module missing'));
      return import(script.src).then(function (module) {
        var values = Object.values(module);
        var apply = values.find(function (value) {
          return typeof value === 'function' && value.name === 'applyAuthTokens';
        });
        if (!apply) throw new Error('applyAuthTokens unavailable');
        apply({ token: token, expire_time: String(Date.now() + 7 * 86400000) });
      });
    }

    // The official SDK login broadcasts this event so the pet window hydrates the
    // new session, reconnects the socket and restores its room. Token login must
    // do the same; reloading only the settings window leaves the pet logged out.
    function emitLoginSuccess() {
      try {
        if (window.__TAURI_INTERNALS__ && window.__TAURI_INTERNALS__.invoke) {
          return Promise.resolve(window.__TAURI_INTERNALS__.invoke('plugin:event|emit', {
            event: 'cat-auth-login-success', payload: {}
          })).catch(function () {});
        }
      } catch (e) {}
      return Promise.resolve();
    }

    function loginWithToken(token) {
      if (!token) {
        showAppToast('请先粘贴 Token。');
        return Promise.resolve(false);
      }
      return applyAuthToken(token).then(emitLoginSuccess).then(function () {
        showAppToast('Token 已写入，正在重新登录…', 1500);
        setTimeout(function () { location.reload(); }, 400);
        return true;
      }).catch(function () {
        showAppToast('Token 登录失败，请检查 Token 是否有效。');
        return false;
      });
    }

    function copyToken(text) {
      if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text);
      }
      return new Promise(function (resolve, reject) {
        try {
          var area = document.createElement('textarea');
          area.value = text;
          area.style.position = 'fixed';
          area.style.opacity = '0';
          area.style.left = '-9999px';
          document.body.appendChild(area);
          area.focus();
          area.select();
          var ok = document.execCommand('copy');
          document.body.removeChild(area);
          if (ok) resolve(); else reject(new Error('copy failed'));
        } catch (e) {
          reject(e);
        }
      });
    }

    return { updateExpiry: updateTokenExpiry, syncLoginVisibility: syncTokenLoginVisibility,
      renew: renewToken, extract: extractToken, showToast: showAppToast,
      login: loginWithToken, emitLoginSuccess: emitLoginSuccess };
  }
