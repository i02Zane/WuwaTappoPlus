import { PlazaController, deriveNativeState } from './controller.mjs';
export { PlazaController };
export const environment = PLAZA_CONFIG.base;

// Observe the app's existing socket on its next native send (including pong).
// Never send a frame, read its auth query, reconnect, or create another socket.
export function observeRoomSocket(root, changed) {
  const proto = root.WebSocket?.prototype;
  const original = proto?.send;
  let active = true, socket = null;
  let detach = () => {};
  function track(candidate) {
    if (!active || socket === candidate) return;
    try {
      const address = new URL(candidate.url);
      if (address.hostname !== 'api-mc-wuwatappo.kurogames-global.com' || !/^\/socket\.io\/?$/.test(address.pathname)) return;
    } catch { return; }
    const replaced = socket !== null;
    detach(); socket = candidate; changed(replaced ? 'replaced' : 'connected');
    const close = () => { if (active && socket === candidate) { socket = null; detach(); changed('disconnected'); } };
    candidate.addEventListener('close', close);
    candidate.addEventListener('error', close);
    detach = () => { candidate.removeEventListener('close', close); candidate.removeEventListener('error', close); detach = () => {}; };
  }
  function send(...args) { track(this); return original.apply(this, args); }
  if (typeof original === 'function') { try { proto.send = send; } catch {} }
  return { connected: () => socket?.readyState === 1,
    dispose() { active = false; if (proto?.send === send) proto.send = original; detach(); socket = null; } };
}

export function start(version) {
  const old = window.__WUT_PLAZA__;
  if (old?.version === version && old.protocol === 3) return old;
  old?.uninstall();
  let disposed = false, timer, rooms = null, networkOnline = true, loggedOut = false, reading = false, lastSnapshotAt = 0;
  const removers = [], waiting = new Map();
  let lastState = null, revision = 0;
  const instance = crypto.randomUUID(); let serial = 0;
  function store() { return document.querySelector('#app')?.__vue_app__?.config.globalProperties.$pinia?._s.get('GlobalState'); }
  const observer = observeRoomSocket(window, () => { rooms = null; });
  function request(op, args) {
    if (disposed) return Promise.reject({ code: 'DISPOSED' });
    if (waiting.size >= 4) return Promise.reject({ code: 'RATE_LIMITED', retry: 30 });
    const id = instance + ':' + (++serial);
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => { waiting.delete(id); reject({ code: 'CONNECTION_FAILED', retry: 30 }); }, 25000);
      waiting.set(id, { resolve, reject, timeout });
      try {
        if (typeof window.__wutPlazaRequest !== 'function') throw new Error('binding unavailable');
        window.__wutPlazaRequest(JSON.stringify({ instance, id, op, args }));
      } catch { waiting.delete(id); clearTimeout(timeout); reject({ code: 'CONNECTION_FAILED', retry: 30 }); }
    });
  }
  const controller = new PlazaController({ request, storage: localStorage, key: 'wut-plaza-v2:' + environment });
  const seen = new Set();
  const api = window.__WUT_PLAZA__ = {
    protocol: 3, version, instance, controller,
    command(command) {
      if (!command || typeof command.id !== 'string' || seen.has(command.id)) return;
      seen.add(command.id); if (seen.size > 64) seen.delete(seen.values().next().value);
      let work;
      if (command.op === 'list') work = controller.list(command.page, command.refresh !== false);
      else if (command.op === 'publish') work = controller.publish();
      else if (command.op === 'withdraw') work = controller.withdraw();
      else if (command.op === 'reset') work = controller.reset();
      publishState();
      void Promise.resolve(work).finally(() => publishState());
    },
    snapshot() { return { ...controller.publicState(), instance, revision }; },
    resend() { publishState(true); },
    complete(results) {
      for (const result of results) {
        const item = waiting.get(result.id); if (!item) continue;
        waiting.delete(result.id); clearTimeout(item.timeout);
        if (result.error) item.reject(result.error); else item.resolve(result.data);
      }
    },
    uninstall() {
      disposed = true; controller.dispose(); clearInterval(timer); observer.dispose();
      waiting.forEach(item => { clearTimeout(item.timeout); item.reject({ code: 'DISPOSED' }); }); waiting.clear();
      removers.forEach(remove => remove());
      if (window.__WUT_PLAZA__ === api) delete window.__WUT_PLAZA__;
    }
  };
  function publishState(force = false) {
    if (disposed || typeof window.__wutPlazaState !== 'function') return;
    const state = controller.publicState();
    const serialized = JSON.stringify(state);
    if (!force && serialized === lastState) return;
    lastState = serialized;
    window.__wutPlazaState(JSON.stringify({ instance, state: { ...state, instance, revision: ++revision } }));
  }
  async function listen(event, handler) {
    const native = window.__TAURI_INTERNALS__;
    const callback = native.transformCallback(payload => { if (!disposed) handler(payload.payload); });
    let eventId;
    try { eventId = await native.invoke('plugin:event|listen', { event, target: { kind: 'Any' }, handler: callback }); }
    catch (error) { native.unregisterCallback?.(callback); throw error; }
    const remove = () => {
      void native.invoke('plugin:event|unlisten', { event, eventId }).catch(() => {});
      native.unregisterCallback?.(callback);
    };
    if (disposed) remove(); else removers.push(remove);
  }
  function roomUpdate(state) {
    if (state?.isLoggedIn === false) { rooms = null; return; }
    if (!state || typeof state.currentRoomId !== 'string' || !['idle', 'owner', 'member'].includes(state.roomMode)) return;
    const account = String(store()?.userInfo?.id || '');
    if (!account || String(state.localMemberId || state.userInfo?.id || '') !== account) return;
    loggedOut = false;
    rooms = { currentRoomId: state.currentRoomId, roomMode: state.roomMode, account };
  }
  void Promise.all([listen('cat-panel-state', roomUpdate), listen('cat-panel-room-members-patch', roomUpdate),
    listen('cat-auth-logout', () => { rooms = null; loggedOut = true; }),
    listen('cat-native-network-status-changed', state => { if (typeof state?.internetAccess === 'boolean') { networkOnline = state.internetAccess; if (!networkOnline) rooms = null; } })
  ]).catch(() => { controller.state.error = '无法读取原生房间状态，暂不发布或续期。'; });
  async function tick() {
    if (disposed || reading) return;
    let native = deriveNativeState(store(), rooms, networkOnline && observer.connected(), loggedOut);
    if ((!rooms || (controller.intent && Date.now() >= controller.retryAt)) && Date.now() - lastSnapshotAt >= 15000) {
      lastSnapshotAt = Date.now(); reading = true;
      try {
        await window.__TAURI_INTERNALS__.invoke('plugin:event|emit', { event: 'cat-panel-action', payload: { type: 'ready' } });
        await new Promise(resolve => setTimeout(resolve, 100));
        native = deriveNativeState(store(), rooms, networkOnline && observer.connected(), loggedOut);
      } catch { controller.state.error = '无法同步原生房间状态，请稍后重试。'; }
      finally { reading = false; }
    }
    if (disposed) return;
    controller.update(native);
    const work = controller.tick(); publishState();
    void Promise.resolve(work).finally(() => publishState());
  }
  timer = setInterval(tick, 1000); void tick(); publishState();
  return api;
}
