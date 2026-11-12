  function createAppsDialog(readApps, isOnline, request, render) {
    var opened = false;
    var appList = null;
    var loading = false;

    function saveGameModeApps(next) {
      if (!isOnline()) { render(); return; }
      request({ key: 'game_mode_apps', value: next });
      render();
    }

    function addGameModeApp(name) {
      if (!isOnline()) { render(); return; }
      request({ action: 'whitelist_add', name: name });
      render();
    }

    function renderAppsRow() {
      var button = document.getElementById('wut-game-apps-manage');
      if (!button) return;
      var apps = readApps();
      var label = apps.length ? '管理白名单（' + apps.length + '）' : '选择应用…';
      if (button.textContent !== label) button.textContent = label;
      button.disabled = !isOnline();
    }

    function closeAppsDialog() {
      opened = false;
      var mask = document.getElementById('wut-apps-modal');
      if (mask) mask.remove();
    }

    function openAppsDialog() {
      if (!isOnline()) { render(); return; }
      if (document.getElementById('wut-apps-modal')) return;
      opened = true;
      appList = null;
      loading = true;
      request({ action: 'list_apps' });
      renderAppsDialog();
    }

    function mountAppsDialog() {
      var mask = document.createElement('div');
      mask.id = 'wut-apps-modal';
      mask.className = 'wut-apps-mask';
      mask.setAttribute('role', 'dialog');
      mask.setAttribute('aria-modal', 'true');
      mask.setAttribute('aria-label', '自动游戏模式白名单');
      var panel = document.createElement('div');
      panel.className = 'wut-apps-panel';
      var head = document.createElement('div');
      head.className = 'wut-apps-head';
      var title = document.createElement('strong');
      title.textContent = '自动游戏模式白名单';
      var close = document.createElement('button');
      close.id = 'wut-apps-close';
      close.type = 'button';
      close.className = 'wut-apps-close';
      close.setAttribute('aria-label', '关闭');
      close.textContent = '×';
      close.addEventListener('click', closeAppsDialog);
      head.appendChild(title);
      head.appendChild(close);
      var chosenTitle = document.createElement('div');
      chosenTitle.className = 'wut-apps-title';
      chosenTitle.textContent = '已添加';
      var chosen = document.createElement('div');
      chosen.id = 'wut-apps-chosen';
      chosen.className = 'wut-game-apps';
      var empty = document.createElement('div');
      empty.id = 'wut-apps-empty';
      empty.className = 'wut-game-apps-empty';
      empty.textContent = '尚未添加程序';
      var openTitle = document.createElement('div');
      openTitle.className = 'wut-apps-title';
      openTitle.textContent = '当前打开的应用';
      var open = document.createElement('div');
      open.id = 'wut-apps-open';
      open.className = 'wut-apps-open';
      var foot = document.createElement('div');
      foot.className = 'wut-apps-foot';
      var input = document.createElement('input');
      input.id = 'wut-apps-input';
      input.type = 'text';
      input.className = 'setting-input';
      input.placeholder = '或输入程序名，例如 SomeGame.exe';
      input.setAttribute('aria-label', '程序名');
      var manual = document.createElement('button');
      manual.id = 'wut-apps-manual';
      manual.type = 'button';
      manual.className = 'setting-btn';
      manual.textContent = '添加';
      function addByName() {
        var value = String(input.value || '').trim();
        if (!value) { return; }
        input.value = '';
        addGameModeApp(value);
      }
      manual.addEventListener('click', addByName);
      input.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') { event.preventDefault(); addByName(); }
      });
      foot.appendChild(input);
      foot.appendChild(manual);
      panel.appendChild(head);
      panel.appendChild(chosenTitle);
      panel.appendChild(chosen);
      panel.appendChild(empty);
      panel.appendChild(openTitle);
      panel.appendChild(open);
      panel.appendChild(foot);
      mask.appendChild(panel);
      mask.addEventListener('click', function (event) {
        if (event.target === mask) closeAppsDialog();
      });
      (document.body || document.documentElement).appendChild(mask);
    }

    function renderAppsDialog() {
      var mask = document.getElementById('wut-apps-modal');
      if (!opened) {
        if (mask) closeAppsDialog();
        return;
      }
      if (!mask) {
        mountAppsDialog();
        mask = document.getElementById('wut-apps-modal');
      }
      if (!mask) return;
      var apps = readApps();
      var chosen = document.getElementById('wut-apps-chosen');
      var key = JSON.stringify(apps);
      if (chosen && chosen.dataset.state !== key) {
        chosen.dataset.state = key;
        while (chosen.firstChild) chosen.removeChild(chosen.firstChild);
        apps.forEach(function (name) {
          var chip = document.createElement('span');
          chip.className = 'wut-game-app';
          var text = document.createElement('span');
          text.textContent = name;
          var remove = document.createElement('button');
          remove.type = 'button';
          remove.className = 'setting-shortcut-clear';
          remove.setAttribute('aria-label', '移除 ' + name);
          remove.textContent = '×';
          remove.addEventListener('click', function () {
            saveGameModeApps(apps.filter(function (item) { return item !== name; }));
          });
          chip.appendChild(text);
          chip.appendChild(remove);
          chosen.appendChild(chip);
        });
      }
      var empty = document.getElementById('wut-apps-empty');
      if (empty) empty.style.display = apps.length ? 'none' : '';
      var open = document.getElementById('wut-apps-open');
      if (!open) return;
      var state = (loading ? 'loading' : JSON.stringify(appList || [])) +
        '|' + JSON.stringify(apps);
      if (open.dataset.state === state) return;
      open.dataset.state = state;
      while (open.firstChild) open.removeChild(open.firstChild);
      if (loading) {
        var loadingNode = document.createElement('div');
        loadingNode.className = 'wut-apps-note';
        loadingNode.textContent = '正在读取…';
        open.appendChild(loadingNode);
        return;
      }
      var list = (appList || []).filter(function (app) { return app && app.name; });
      if (!list.length) {
        var none = document.createElement('div');
        none.className = 'wut-apps-note';
        none.textContent = '没有可添加的应用';
        open.appendChild(none);
        return;
      }
      list.forEach(function (app) {
        var row = document.createElement('button');
        row.type = 'button';
        row.className = 'wut-apps-item';
        var label = document.createElement('span');
        label.className = 'wut-apps-item-name';
        label.textContent = app.name;
        var text = document.createElement('span');
        text.className = 'wut-apps-item-title';
        text.textContent = app.title || '';
        row.appendChild(label);
        row.appendChild(text);
        if (apps.indexOf(String(app.name).toLowerCase()) >= 0) {
          row.classList.add('is-added');
          var mark = document.createElement('span');
          mark.className = 'wut-apps-item-state';
          mark.textContent = '已添加';
          row.appendChild(mark);
          row.disabled = true;
        } else {
          row.addEventListener('click', function () { addGameModeApp(app.name); });
        }
        open.appendChild(row);
      });
    }

    return { get isOpen() { return opened; }, get list() { return appList; },
      get loading() { return loading; },
      receive: function (apps) { appList = Array.isArray(apps) ? apps : []; loading = false; },
      open: openAppsDialog, close: closeAppsDialog,
      renderRow: renderAppsRow, render: renderAppsDialog };
  }
