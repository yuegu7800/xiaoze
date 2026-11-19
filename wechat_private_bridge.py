"""Explicit, single-owner WeChat bridge. Import has no runtime side effects."""
from dataclasses import dataclass
import threading
from core import MessageCursor, ScopedTransport, ScopeError, SendUncertainError
from character_service import Cancelled

SCENE = 'wechat-owner'


class OwnerScope:
    is_group = False
    def __init__(self, target):
        if not isinstance(target, str) or not target.strip():
            raise ValueError('请明确绑定主人私聊完整标题。')
        self.target = target.strip()
    def check(self, name, is_group):
        if is_group is not False or name != self.target:
            raise ScopeError('不是明确绑定的主人私聊；未授权读取/发送。')
        return 'owner'


@dataclass(frozen=True)
class SendEvidence:
    # Only an independently implemented transport with actual evidence may return this.
    layer: str


class SendNotStarted(ScopeError):
    """Explicit evidence that the backend send_text boundary was never entered."""


class VisualTransport(ScopedTransport):
    def send(self, name, text):
        # Same scoped preflight as ScopedTransport, with an explicit exception
        # contract: a ScopeError FROM send_text is not proof of non-delivery.
        try:
            if not self.can_send():
                raise ScopeError('已暂停')
            self.scope.check(name, False)
            if self.identity(foreground=True) != name:
                raise ScopeError('目标变化')
            if not self.can_send():
                raise ScopeError('已取消本轮')
        except ScopeError as error:
            raise SendNotStarted('底层发送前门槛拒绝，未调用send_text') from error
        # Irreversible dispatch boundary: no lock is held across native I/O.
        # Cancellation after admission cannot recall a pending/native keypress.
        try:
            checked = getattr(type(self.backend), 'send_text_checked', None)
            if callable(checked):
                checked(self.backend, name, text, self.can_send)
            else:
                self.backend.send_text(name, text)
        except Exception as error:
            raise SendUncertainError('底层发送已进入，异常不能证明未发送') from error
        # True proves only press(enter) returned; False can follow partial effects.
        raise SendUncertainError('粘贴/回车结果不确定；未证明微信接受或对方收到，请核对。')


class PrivateBridge:
    def __init__(self, core, transport, complete, target):
        self.core, self.transport, self.complete = core, transport, complete
        self.scope = OwnerScope(target)
        self.target = self.scope.target
        self.cursor = MessageCursor()
        self.lock = threading.RLock()
        self.connected = False
        self.closed = False
        self.mode = 'paused'
        self.epoch = 0
        self.busy = False
        self._active = None
        self.last = {}
        transport.can_send = self.can_send

    def _current(self, epoch, mode):
        # Called only with the state lock held; no external code or I/O here.
        return (epoch == self.epoch and mode == self.mode and self.connected
                and not self.closed)

    def _require_current(self, epoch, mode):
        with self.lock:
            if not self._current(epoch, mode):
                raise Cancelled('本轮许可已撤销')

    def can_send(self):
        with self.lock:
            return bool(self.busy and self._active is not None
                        and self._active[1] == 'live' and self._current(*self._active))

    def _revoke(self, *, disconnect=False, close=False):
        with self.lock:
            self.epoch += 1
            self.mode = 'paused'
            if disconnect or close:
                self.connected = False
            if close:
                self.closed = True
        # Never acquire the core lock while holding the bridge state lock.
        self.core.invalidate()

    def pause(self):
        self._revoke()

    def disconnect(self):
        self._revoke(disconnect=True)

    def close(self):
        self._revoke(close=True)

    def _baseline(self, mode=None):
        with self.lock:
            if (self.closed or self.busy or
                    (mode is not None and not self.connected)):
                raise RuntimeError('已关闭、未连接或仍有操作在途。')
            self.busy = True
            self.epoch += 1
            self.mode = 'paused'
            epoch = self.epoch
        try:
            self.core.invalidate()
            name, messages = self.transport.read()
            self.scope.check(name, False)
            with self.lock:
                if epoch != self.epoch or self.closed:
                    raise Cancelled('基线读取期间已取消，不恢复连接或模式。')
                self.cursor.seed(name, messages)
                self.connected = True
                self.mode = mode or 'paused'
        finally:
            with self.lock:
                self.busy = False

    def connect(self):
        self._baseline()

    def enable(self, mode):
        if mode not in ('preview', 'live'):
            raise RuntimeError('无效模式')
        # Discard everything before explicit activation. Busy also protects this
        # slow read from reentrant step/enable/connect, and epoch guards its end.
        self._baseline(mode)

    def step(self):
        with self.lock:
            if not self.connected or self.closed or self.mode == 'paused' or self.busy:
                return
            self.busy = True
            epoch, mode = self.epoch, self.mode
            self._active = (epoch, mode)
        state = dict(generated=False, local_committed=False, send_attempted=False,
                     result='known_unsent', proof='无外部发送证据', reply='')
        prepared = None
        try:
            ticket = self.core.ticket()
            self._require_current(epoch, mode)
            name, messages = self.transport.read()
            self._require_current(epoch, mode)
            self.scope.check(name, False)
            with self.lock:
                if not self._current(epoch, mode):
                    raise Cancelled('收件期间已取消')
                fresh = self.cursor.take(name, messages)
            if not fresh:
                return
            text = '\n'.join(m.content for m in fresh)
            self._require_current(epoch, mode)
            def complete(messages):
                # Also guard the actual model callback, after core assembly.
                self._require_current(epoch, mode)
                return self.complete(messages)
            complete.model_id = getattr(self.complete, 'model_id', 'wechat')
            reply, commit = self.core.prepare_exchange(
                SCENE, 'owner', text, complete, ticket=ticket)
            prepared = commit
            state.update(generated=True, reply=reply)
            self._require_current(epoch, mode)
            if mode == 'preview':
                state['result'] = 'preview'
                self.pause()
                return
            # Slow OCR/title checks and sending must never hold the state lock.
            self.scope.check(self.transport.identity(), False)
            self._require_current(epoch, mode)
            state['send_attempted'] = True
            try:
                evidence = self.transport.send(self.target, reply)
            except SendNotStarted:
                state.update(result='known_unsent', proof='底层发送前门槛拒绝，未调用send_text')
                self.pause()
                return
            except Exception:
                # Even ScopeError can originate inside send_text AFTER effects.
                state.update(result='uncertain', proof='可能已发送；请人工核对，禁止自动重发')
                self.pause()
                return
            if not isinstance(evidence, SendEvidence) or not evidence.layer.strip():
                state.update(result='uncertain', proof='返回值不含可靠接收证据；请核对')
                self.pause()
                return
            state.update(result='confirmed', proof=evidence.layer)
            try:
                self._require_current(epoch, mode)
                commit()
                state['local_committed'] = True
            except Exception:
                state['proof'] += '；本地提交失败或已取消，禁止重发'
                self.pause()
        except Cancelled:
            state['result'] = 'cancelled'
        except Exception:
            state['result'] = 'failed_known_unsent'
            self.pause()
        finally:
            if prepared is not None:
                prepared.discard()
            with self.lock:
                self.last = state
                self._active = None
                self.busy = False


def create_core(path):
    from pathlib import Path
    from character import Person, Scene
    from character_service import CharacterCoreBase
    from memory_store import MemoryStore
    scene = Scene(SCENE, '微信主人单一私聊', 'private', (Person('owner', '孤月大人', 'owner'),))
    return CharacterCoreBase(Path(__file__).with_name('persona.txt'),
                             MemoryStore(path, {SCENE: {'owner': 'owner'}}), {SCENE: scene})
