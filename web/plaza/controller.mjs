// Shared lifecycle; official room state stays authoritative.
const roomCode = value => typeof value === 'string' && /^[0-9A-Za-z]{4,16}$/.test(value);
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
export class PlazaController {
  constructor({ request, storage, now = Date.now, key = 'wut-plaza-v2', makeId = () => crypto.randomUUID() }) {
    Object.assign(this, { request, storage, now, key, makeId });
    this.native = { logged: false, known: false, account: '', room: '', owner: false, connected: false };
    this.state = { busy: false, error: '', published: false, expiresAt: null, items: [], page: 0, hasMore: false, loading: false, authExpired: false };
    this.intent = null; this.pendingWrite = null; this.withdrawing = false; this.retryAt = 0; this.nextListAt = 0; this.disposed = false;
    this.pages = {}; this.offset = 0; this.identityEpoch = null; this.identityVerified = false; this.hadSaved = false;
    try {
      const saved = JSON.parse(storage.getItem(key) || 'null');
      this.identityEpoch = saved?.identityEpoch || null;
      this.hadSaved = !!(saved?.intent || saved?.pendingWrite || saved?.withdrawing);
      if (saved?.intent && typeof saved.intent.account === 'string' && roomCode(saved.intent.room)) this.intent = saved.intent;
      if (saved?.pendingWrite && ['publish', 'withdraw'].includes(saved.pendingWrite.op) && uuid(saved.pendingWrite.key) &&
          (saved.pendingWrite.generation == null || uuid(saved.pendingWrite.generation)) &&
          (saved.pendingWrite.op !== 'publish' || roomCode(saved.pendingWrite.room_code))) this.pendingWrite = saved.pendingWrite;
      this.withdrawing = saved?.withdrawing === true;
      const cache = JSON.parse(storage.getItem(key + ':cache') || 'null');
      if (cache && Number.isFinite(cache.nextListAt)) this.nextListAt = Math.min(cache.nextListAt, now() + 30000);
      if (cache?.pages && typeof cache.pages === 'object') {
        for (const [page, entry] of Object.entries(cache.pages).slice(0, 50)) {
          if (/^\d+$/.test(page) && Number(page) < 50 && Array.isArray(entry.items) && entry.items.length <= 20) this.pages[page] = entry;
        }
        if (this.pages[cache.page]) this.applyPage(Number(cache.page), this.pages[cache.page]);
      }
    } catch { this.state.error = '本地广场记录无法读取，请检查本地存储。'; }
  }
  persist() {
    this.storage.setItem(this.key, JSON.stringify({ intent: this.intent, pendingWrite: this.pendingWrite, withdrawing: this.withdrawing, identityEpoch: this.identityEpoch }));
  }
  async identify() {
    const own = await this.request('own', {});
    if (this.disposed) return null;
    if (typeof own.identity_epoch !== 'string' || !/^[a-f0-9]{64}$/.test(own.identity_epoch)) throw new Error('INVALID_RESPONSE');
    const changed = this.hadSaved && this.identityEpoch !== own.identity_epoch;
    this.identityEpoch = own.identity_epoch; this.identityVerified = true; this.hadSaved = false;
    if (changed) {
      this.intent = null; this.pendingWrite = null; this.withdrawing = false; this.state.published = false;
      this.state.error = '广场身份已变化，旧发布意愿已清除，请重新确认发布。';
    }
    this.persist(); return { ...own, identityChanged: changed };
  }
  cache() {
    try { this.storage.setItem(this.key + ':cache', JSON.stringify({ pages: this.pages, page: this.state.page, nextListAt: this.nextListAt })); }
    catch { this.state.error = '列表缓存无法保存，当前仍使用内存缓存。'; }
  }
  update(native) { this.native = { ...native }; }
  eligible() { return this.native.known && this.native.logged && this.native.connected && this.native.owner && roomCode(this.native.room); }
  matches(intent = this.intent) { return !!intent && this.eligible() && intent.account === this.native.account && intent.room === this.native.room; }
  publicState() {
    return { ...this.state, nextRefreshAt: this.nextListAt, serverOffset: this.offset, items: this.native.logged ? this.state.items.filter(item => roomCode(item?.room_code) && Date.parse(item.expires_at) > this.now() + this.offset) : [],
      canWithdraw: !!(this.intent || this.pendingWrite || this.withdrawing), withdrawing: this.withdrawing,
      withdrawRoom: this.intent?.room || this.pendingWrite?.room_code || null,
      logged: this.native.logged, owner: this.eligible(), room: this.native.room,
      published: this.state.published && this.matches() && !this.withdrawing && Date.parse(this.state.expiresAt) > this.now() + this.offset };
  }
  fail(error) {
    const messages = { ROOM_ALREADY_PUBLISHED: '这个房间已被发布，请等待原公告撤下或到期。',
      GENERATION_CONFLICT: '房间发布状态已变化，将重新核对。', IDEMPOTENCY_CONFLICT: '发布请求状态冲突，请稍后重试。',
      MAINTENANCE: '广场正在维护，请稍后重试。', PUBLISHING_DISABLED: '广场暂时停止接受新发布。',
      UNAUTHORIZED: '广场身份已失效，请点击“重建广场身份”。', IDENTITY_STORAGE_ERROR: '广场身份无法读取或保存，请点击“重建广场身份”。',
      RATE_LIMITED: '操作过于频繁，请等待倒计时后重试。', LIST_COOLDOWN: '刷新过于频繁，请稍后重试。', WRITE_COOLDOWN: '发布过于频繁，请稍后重试。' };
    this.state.error = messages[error?.code] || '广场连接失败，请稍后重试。';
    if (['UNAUTHORIZED', 'IDENTITY_STORAGE_ERROR'].includes(error?.code)) this.state.authExpired = true;
    const retry = Math.max(1000, Math.min(3600000, (Number(error?.retry) || 60) * 1000));
    this.retryAt = this.now() + retry;
    if (error?.code === 'GENERATION_CONFLICT') this.pendingWrite = null;
    return retry;
  }
  applyPage(page, entry) { this.state.items = entry.items; this.state.page = page; this.state.hasMore = entry.hasMore === true; }
  async list(page = 0, refresh = true) {
    if (!this.native.logged || this.state.loading || this.disposed || this.state.authExpired) return;
    page = Math.max(0, Math.min(49, Number.isInteger(page) ? page : 0));
    if (refresh && this.now() < this.nextListAt) return;
    if (!refresh && this.pages[page] && this.now() < this.nextListAt) { this.applyPage(page, this.pages[page]); this.cache(); return; }
    if (refresh) this.nextListAt = this.now() + 30000;
    this.cache(); this.state.loading = true; this.state.error = '';
    try {
      const result = await this.request('list', { page, refresh });
      if (this.disposed) return;
      if (!Array.isArray(result.items) || result.items.length > 20 || result.page !== page || typeof result.has_more !== 'boolean') throw new Error('INVALID_RESPONSE');
      this.offset = Date.parse(result.server_time) - this.now();
      this.nextListAt = Math.max(this.nextListAt, this.now() + Math.max(0, Date.parse(result.next_refresh_at) - Date.parse(result.server_time)));
      if (refresh) this.pages = {};
      const entry = { items: result.items, hasMore: result.has_more };
      this.pages[page] = entry; this.applyPage(page, entry); this.cache();
    } catch (error) { if (!this.disposed) { const wait = this.fail(error); this.nextListAt = Math.max(this.nextListAt, this.now() + wait); this.cache(); } }
    finally { this.state.loading = false; }
  }
  async publish(automatic = false) {
    if (!this.eligible() || this.state.busy || this.disposed || this.withdrawing || this.state.authExpired || (automatic && !this.matches())) return;
    const native = { ...this.native };
    this.state.busy = true; this.state.error = '';
    let compensate = false;
    try {
      if (!automatic) { this.intent = { account: native.account, room: native.room }; this.persist(); }
      const identity = !this.identityVerified ? await this.identify() : null;
      if (this.disposed || identity?.identityChanged) return;
      if (!this.pendingWrite) {
        const own = identity || await this.request('own', {});
        if (this.disposed) return;
        this.offset = Date.parse(own.server_time) - this.now();
        if (!this.matches() || this.withdrawing) { this.withdrawing = true; this.intent = null; this.persist(); compensate = true; return; }
        if (automatic && own.item?.room_code === native.room) {
          const renewAt = Date.parse(own.item.updated_at) + 300000 - this.offset;
          if (renewAt > this.now() && Date.parse(own.item.expires_at) > this.now() + this.offset) {
            this.state.published = true; this.state.expiresAt = own.item.expires_at;
            this.retryAt = renewAt; this.persist(); return;
          }
        }
        if (own.item && own.item.room_code !== native.room) {
          this.pendingWrite = { op: 'withdraw', generation: own.item.generation, key: this.makeId() }; this.persist();
          await this.request('withdraw', { generation: own.item.generation, key: this.pendingWrite.key });
          this.pendingWrite = null; this.persist();
          if (!this.matches() || this.withdrawing) { compensate = true; return; }
        }
        this.pendingWrite = { op: 'publish', account: native.account, room_code: native.room,
          generation: own.item?.room_code === native.room ? own.item.generation : null, key: this.makeId() };
        this.persist();
      }
      if (this.pendingWrite.op !== 'publish' || this.pendingWrite.account !== native.account || this.pendingWrite.room_code !== native.room) {
        this.withdrawing = true; this.intent = null; this.persist(); compensate = true; return;
      }
      const { key, generation, room_code } = this.pendingWrite;
      const result = await this.request('publish', { key, generation, room_code });
      if (this.disposed) return;
      if (!result.item || !uuid(result.item.generation) || result.item.room_code !== native.room) throw new Error('INVALID_RESPONSE');
      this.pendingWrite = null;
      if (!this.matches() || this.withdrawing) {
        this.pendingWrite = { op: 'withdraw', generation: result.item.generation, key: this.makeId() };
        this.intent = null; this.withdrawing = true; this.persist(); compensate = true;
      } else {
        this.state.published = true; this.state.expiresAt = result.item.expires_at;
        this.retryAt = Date.parse(result.item.expires_at) > this.now() + this.offset ? this.now() + 300000 : 0; this.persist();
      }
    } catch (error) { if (!this.disposed) { this.fail(error); try { this.persist(); } catch {} } }
    finally { this.state.busy = false; if (compensate && !this.disposed) await this.withdraw(); }
  }
  async withdraw() {
    if (this.disposed) return;
    this.intent = null; this.withdrawing = true; this.state.published = false;
    try { this.persist(); } catch { this.state.error = '无法保存撤下状态，请检查本地存储。'; return; }
    if (this.state.busy || this.state.authExpired) return;
    this.state.busy = true; this.state.error = '';
    try {
      const identity = !this.identityVerified ? await this.identify() : null;
      if (this.disposed || identity?.identityChanged) return;
      if (this.pendingWrite?.op !== 'withdraw') {
        const own = identity || await this.request('own', {});
        if (this.disposed) return;
        this.pendingWrite = own.item ? { op: 'withdraw', generation: own.item.generation, key: this.makeId() } : null;
        this.persist();
      }
      if (this.pendingWrite) await this.request('withdraw', { generation: this.pendingWrite.generation, key: this.pendingWrite.key });
      if (this.disposed) return;
      this.pendingWrite = null; this.withdrawing = false; this.state.expiresAt = null; this.persist();
    } catch (error) { if (!this.disposed) { this.fail(error); if (!this.state.authExpired) this.state.error = '撤下暂未确认，正在等待重试；公告会自行到期。'; try { this.persist(); } catch {} } }
    finally { this.state.busy = false; }
  }
  async reset() {
    if (this.state.busy || !this.state.authExpired || this.disposed) return;
    this.state.busy = true;
    try {
      await this.request('reset', {});
      this.intent = null; this.pendingWrite = null; this.withdrawing = false; this.state.published = false; this.persist();
      this.identityEpoch = null; this.identityVerified = false; this.hadSaved = false;
      this.state.authExpired = false; this.state.error = ''; this.nextListAt = 0; this.pages = {}; this.cache();
    } catch (error) { this.fail(error); }
    finally { this.state.busy = false; }
  }
  async tick() {
    if (this.disposed || this.state.busy || this.state.authExpired) return;
    if (this.withdrawing) { if (this.now() >= this.retryAt) await this.withdraw(); return; }
    if (!this.intent) return;
    if (this.native.logged && this.native.account !== this.intent.account) { await this.withdraw(); return; }
    if (!this.native.known) return;
    if (!this.native.logged || this.native.account !== this.intent.account || this.native.room !== this.intent.room || (this.native.connected && !this.native.owner)) { await this.withdraw(); return; }
    if (this.matches() && this.now() >= this.retryAt) await this.publish(true);
  }
  dispose() { this.disposed = true; }
}

export function deriveNativeState(store, rooms, connected = false, loggedOut = false) {
  const account = store?.userInfo?.id;
  const logged = store?.isLoggedIn === true && Number.isSafeInteger(account) && account > 0;
  const room = String(store?.userInfo?.room_no || '').trim();
  const matches = !!rooms && rooms.account === String(account) && rooms.currentRoomId === room;
  return { logged, account: logged ? String(account) : '', room,
    known: logged ? matches : loggedOut === true,
    owner: matches && rooms.roomMode === 'owner', connected: connected === true };
}
