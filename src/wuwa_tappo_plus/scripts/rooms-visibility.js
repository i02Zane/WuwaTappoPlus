  function createRoomVisibility(inRoom, ensureStyle) {
    var VISIBILITY = 'wut-room-visibility';
    var visibilityUntil = 0;
    var visibilityTarget = false;
    function memberButtons(container) {
      // The native panel renders an eye only for an online, non-local member.
      return Array.from(container.querySelectorAll('.room-members .room-visible-btn')).filter(function (button) {
        return !button.disabled;
      });
    }

    function nativeVisibilityIcons() {
      var icons = {};
      Array.from(document.styleSheets || []).forEach(function (sheet) {
        try {
          Array.from(sheet.cssRules || []).forEach(function (rule) {
            var selector = rule.selectorText || '';
            if (selector.indexOf('.room-visible-btn') < 0 || selector.indexOf(':') >= 0 || !rule.style || !rule.style.backgroundImage) return;
            icons[selector.indexOf('.room-invisible') >= 0 ? 'hide' : 'show'] = rule.style.backgroundImage;
          });
        } catch (e) { /* A changed/cross-origin stylesheet uses the inline fallback. */ }
      });
      return icons;
    }

    function mountVisibility(container) {
      var leave = container.querySelector('.leave-room-btn');
      var members = container.querySelector('.room-members');
      var control = document.getElementById(VISIBILITY);
      if (!inRoom() || !leave || !members || !members.parentNode) {
        if (control) control.remove();
        visibilityUntil = 0;
        return;
      }
      ensureStyle();
      if (!control) {
        control = document.createElement('div'); control.id = VISIBILITY;
        var title = document.createElement('span'); title.className = 'wut-members-title'; title.textContent = '房间成员'; control.appendChild(title);
        var icons = nativeVisibilityIcons();
        [[true, '显示他人桌宠', 'show'], [false, '隐藏他人桌宠', 'hide']].forEach(function (definition) {
          var button = document.createElement('button');
          button.id = VISIBILITY + '-' + definition[2]; button.type = 'button';
          button.className = 'room-visible-btn wut-room-eye' + (definition[0] ? '' : ' room-invisible');
          button.title = definition[1]; button.setAttribute('aria-label', definition[1]);
          if (icons[definition[2]]) button.style.backgroundImage = icons[definition[2]];
          else button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>'
            + (definition[0] ? '' : '<path d="m3 3 18 18"/>') + '</svg>';
          button.addEventListener('click', function (event) {
            event.preventDefault(); event.stopPropagation();
            if (visibilityUntil > Date.now() || !inRoom()) return;
            var current = document.querySelector('.room-module .panel-content');
            if (!current || !current.contains(control)) return;
            var show = definition[0];
            // Snapshot once: IPC updates may arrive after all native clicks return.
            var targets = memberButtons(current).filter(function (eye) {
              return eye.classList.contains('room-invisible') === show;
            });
            if (!targets.length) return;
            visibilityTarget = show;
            visibilityUntil = Date.now() + 2000;
            control.querySelectorAll('button').forEach(function (item) { item.disabled = true; });
            targets.forEach(function (eye) { eye.click(); });
            mountVisibility(current);
          });
          control.appendChild(button);
        });
        members.parentNode.insertBefore(control, members);
      } else if (control.parentNode !== members.parentNode || control.nextElementSibling !== members) {
        members.parentNode.insertBefore(control, members);
      }
      var buttons = memberButtons(container);
      var visible = buttons.filter(function (button) { return !button.classList.contains('room-invisible'); }).length;
      if (visibilityUntil && (visibilityTarget ? visible === buttons.length : visible === 0)) visibilityUntil = 0;
      var pending = visibilityUntil > Date.now();
      var disabled = pending || !buttons.length || leave.disabled;
      control.querySelector('#' + VISIBILITY + '-show').disabled = disabled || visible === buttons.length;
      control.querySelector('#' + VISIBILITY + '-hide').disabled = disabled || visible === 0;
      control.setAttribute('aria-busy', pending ? 'true' : 'false');
      control.title = buttons.length ? '仅切换当前在线的其他桌宠，不影响自己的桌宠' : '当前没有可切换的其他桌宠';
    }

    return { mount: mountVisibility, dispose: function () {
      var control = document.getElementById(VISIBILITY);
      if (control) control.remove();
      visibilityUntil = 0;
    } };
  }
