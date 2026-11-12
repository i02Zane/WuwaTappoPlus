"""Construct and release the daemon's feature resources as one lifetime."""

from . import cdp, fullscreen, game_mode
from .chests import ChestCache
from .observations import PageObservations
from .pages import TargetDiscovery
from .pet_page import PetPage
from .plaza import PlazaBridge
from .reports import DailyReport
from .rooms import RoomBridge, RoomHistory
from .screens import ScreenKeeper
from .settings import SettingsBridge
from .toasts import ToastBridge


def sample_game_mode(cfg):
    """Unknown foreground samples must not be mistaken for leaving game mode."""
    covered, name = fullscreen.foreground_window()
    if covered is None:
        return None
    return covered or bool(name and name.lower() in cfg.game_mode_apps)


def attempt(log, label, action, default=None):
    """Keep a feature failure local; process control exceptions still propagate."""
    try:
        return action()
    except Exception as exc:
        log.warn("%s: %s" % (label, str(exc)[:160]))
        return default


class RuntimeServices:
    def __init__(self, cfg, log, detector):
        self.log = log
        try:
            self.discovery = TargetDiscovery(cfg.port, cfg.host)
            self.session = cdp.Session(cfg.port, cfg.host, discovery=self.discovery)
            self.pet = PetPage(self.session, log)
            self.observations = PageObservations(log)
            self.settings = SettingsBridge(cfg.port, cfg.host, log, discovery=self.discovery)
            self.reports = DailyReport(log)
            self.rooms = RoomBridge(cfg.port, cfg.host, log, discovery=self.discovery)
            self.plaza = PlazaBridge(log)
            self.history = RoomHistory(log, limit=cfg.room_history)
            self.toasts = ToastBridge(cfg.port, cfg.host, log, discovery=self.discovery)
            self.screens = ScreenKeeper(log)
            self.chests = ChestCache(log)
            self.game_mode = game_mode.GameModeController(log, detector=detector)
        except BaseException:
            self.close()
            raise

    def close(self):
        # Restore ownership while the main page is still connected. A failed
        # restore keeps the page's existing recovery ledger for next startup.
        session = getattr(self, "session", None)
        controller = getattr(self, "game_mode", None)
        if controller is not None and session is not None and session.ws is not None:
            attempt(self.log, "game mode restore pending", lambda: controller.release(session))
        for name in ("settings", "reports", "rooms", "history", "plaza", "toasts", "session"):
            resource = getattr(self, name, None)
            if resource is not None:
                action = (lambda: resource.close(session)) if name == "plaza" else resource.close
                attempt(self.log, "close " + name, action)
