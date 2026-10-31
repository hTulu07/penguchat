#!/usr/bin/env python3
"""Penguchat: a small, native-feeling WhatsApp Web desktop app for Linux (PyQt6 + QtWebEngine)."""

import re
import sys
from pathlib import Path

from PyQt6.QtCore import QMetaType, QSettings, QStandardPaths, Qt, QTimer, QUrl, pyqtSlot
from PyQt6.QtDBus import QDBusArgument, QDBusConnection, QDBusMessage
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QShortcut
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QMainWindow, QMenu, QSystemTrayIcon

APP_ID = "io.github.htulu07.Penguchat"  # desktop file, icon and Wayland app id
APP_CMD = "penguchat"                    # command, data folders and settings
APP_NAME = "Penguchat"
VERSION = "1.1.0"
HOME_URL = QUrl("https://web.whatsapp.com/")
AUTOSTART_FILE = Path.home() / ".config/autostart" / f"{APP_ID}.desktop"
SOCKET_NAME = f"{APP_CMD}-instance"
IN_FLATPAK = Path("/.flatpak-info").exists()

NOTIFY_SERVICE = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"


def is_whatsapp(url: QUrl) -> bool:
    host = url.host()
    return any(host == d or host.endswith("." + d) for d in ("whatsapp.com", "whatsapp.net"))


def app_icon() -> QIcon:
    icon = QIcon.fromTheme(APP_ID)
    if icon.isNull():
        icon = QIcon(str(Path(__file__).resolve().parent / "data" / f"{APP_ID}.svg"))
    return icon


def badge_icon(base: QIcon, count: int) -> QIcon:
    """Draw the unread count on top of the app icon."""
    if count <= 0:
        return base
    pix = base.pixmap(64, 64)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#ff5f6d"))
    p.drawEllipse(30, 0, 34, 34)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(20 if count < 10 else 16)
    p.setFont(font)
    p.setPen(QColor("white"))
    p.drawText(30, 0, 34, 34, Qt.AlignmentFlag.AlignCenter, str(count) if count < 100 else "99+")
    p.end()
    return QIcon(pix)


class ExternalPage(QWebEnginePage):
    """Catches target=_blank links and hands them to the default browser."""

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() in ("http", "https", "mailto", "tel"):
            QDesktopServices.openUrl(url)
        self.deleteLater()
        return False


class Page(QWebEnginePage):
    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if (nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
                and url.scheme() in ("http", "https") and not is_whatsapp(url)):
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def createWindow(self, _type):
        return ExternalPage(self.profile(), self)

    def javaScriptConsoleMessage(self, *_):
        pass  # WhatsApp's own console noise is not useful in a terminal


class Notifier:
    """Sends web notifications to the desktop through org.freedesktop.Notifications."""

    def __init__(self, window):
        self.window = window
        self.bus = QDBusConnection.sessionBus()
        self.active = {}  # dbus id -> QWebEngineNotification
        self.icon_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / "avatars"
        self.icon_dir.mkdir(parents=True, exist_ok=True)
        for signal in ("ActionInvoked", "NotificationClosed"):
            self.bus.connect(NOTIFY_SERVICE, NOTIFY_PATH, NOTIFY_SERVICE, signal, getattr(window, "on_" + signal))

    def notify(self, summary, body, image="", actions=(), replaces=0):
        msg = QDBusMessage.createMethodCall(NOTIFY_SERVICE, NOTIFY_PATH, NOTIFY_SERVICE, "Notify")
        hints = {"desktop-entry": APP_ID}
        if image:
            hints["image-path"] = image
        msg.setArguments([APP_NAME, QDBusArgument(replaces, QMetaType.Type.UInt.value), APP_ID,
                          summary, body, QDBusArgument(list(actions), QMetaType.Type.QStringList.value), hints, -1])
        reply = self.bus.call(msg)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            return 0
        return int(reply.arguments()[0])

    def show_web(self, notification):
        image = ""
        if not notification.icon().isNull():
            path = self.icon_dir / f"{abs(hash(notification.tag() or notification.title()))}.png"
            notification.icon().save(str(path))
            image = str(path)
        nid = self.notify(notification.title(), notification.message(), image, ["default", "Open"])
        if not nid:
            return
        self.active[nid] = notification
        notification.closed.connect(lambda: self.close(nid))
        notification.show()

    def close(self, nid):
        if self.active.pop(nid, None) is not None:
            msg = QDBusMessage.createMethodCall(NOTIFY_SERVICE, NOTIFY_PATH, NOTIFY_SERVICE, "CloseNotification")
            msg.setArguments([QDBusArgument(nid, QMetaType.Type.UInt.value)])
            self.bus.asyncCall(msg)


class MainWindow(QMainWindow):
    def __init__(self, start_hidden=False):
        super().__init__()
        self.settings = QSettings(APP_CMD, APP_CMD)
        self.base_icon = app_icon()
        self.unread = 0
        self.quitting = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(self.base_icon)
        self.setMinimumSize(480, 400)
        if not self.restoreGeometry(self.settings.value("geometry", b"")):
            self.resize(1200, 800)

        self.profile = self.make_profile()
        self.view = QWebEngineView(self)
        self.page = Page(self.profile, self.view)
        if hasattr(self.page, "permissionRequested"):  # Qt 6.8+
            self.page.permissionRequested.connect(self.on_permission)
        else:
            self.page.featurePermissionRequested.connect(self.on_feature_permission)
        self.page.titleChanged.connect(self.on_title)
        self.view.setPage(self.page)
        self.view.setZoomFactor(float(self.settings.value("zoom", 1.0)))
        self.setCentralWidget(self.view)

        self.notifier = Notifier(self)
        self.profile.setNotificationPresenter(self.notifier.show_web)

        self.make_tray()
        self.make_shortcuts()
        self.view.load(HOME_URL)

        if not (start_hidden and self.tray):
            self.show()

    # --- setup -------------------------------------------------------------

    def make_profile(self):
        profile = QWebEngineProfile(APP_CMD, QApplication.instance())
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        # WhatsApp only accepts browsers it knows, so drop the QtWebEngine token.
        profile.setHttpUserAgent(re.sub(r"\s*QtWebEngine/\S+", "", profile.httpUserAgent()))
        profile.downloadRequested.connect(self.on_download)
        s = profile.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanPaste, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        return profile

    def make_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.base_icon, self)
        self.tray.setToolTip(APP_NAME)
        self.tray.activated.connect(self.on_tray_activated)

        menu = QMenu(self)
        menu.addAction(QIcon.fromTheme("window"), "Show / Hide", self.toggle_visible)
        menu.addAction(QIcon.fromTheme("view-refresh"), "Reload", self.view.reload)
        menu.addSeparator()
        self.close_to_tray_action = self.check_action(menu, "Close to tray", "close_to_tray", True)
        self.autostart_action = QAction("Start at login", menu, checkable=True)
        self.autostart_action.setChecked(
            self.settings.value("autostart", False, type=bool) if IN_FLATPAK else AUTOSTART_FILE.exists())
        self.autostart_action.toggled.connect(self.set_autostart)
        menu.addAction(self.autostart_action)
        menu.addSeparator()
        menu.addAction(QIcon.fromTheme("application-exit"), "Quit", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def check_action(self, menu, text, key, default):
        action = QAction(text, menu, checkable=True)
        action.setChecked(self.settings.value(key, default, type=bool))
        action.toggled.connect(lambda on: self.settings.setValue(key, on))
        menu.addAction(action)
        return action

    def make_shortcuts(self):
        keys = {
            "Ctrl+Q": self.quit,
            "Ctrl+W": self.close,
            "Ctrl+R": self.view.reload,
            "F5": self.view.reload,
            "F11": self.toggle_fullscreen,
            "Ctrl++": lambda: self.zoom(0.1),
            "Ctrl+=": lambda: self.zoom(0.1),
            "Ctrl+-": lambda: self.zoom(-0.1),
            "Ctrl+0": lambda: self.zoom(None),
        }
        for key, fn in keys.items():
            QShortcut(QKeySequence(key), self, activated=fn)

    # --- web events --------------------------------------------------------

    def on_permission(self, permission):
        if is_whatsapp(permission.origin()):
            permission.grant()
        else:
            permission.deny()

    def on_feature_permission(self, origin, feature):
        policy = QWebEnginePage.PermissionPolicy
        granted = policy.PermissionGrantedByUser if is_whatsapp(origin) else policy.PermissionDeniedByUser
        self.page.setFeaturePermission(origin, feature, granted)

    def on_title(self, title):
        m = re.match(r"\((\d+)\)", title)
        count = int(m.group(1)) if m else 0
        if count == self.unread:
            return
        self.unread = count
        self.setWindowTitle(f"({count}) {APP_NAME}" if count else APP_NAME)
        if self.tray:
            self.tray.setIcon(badge_icon(self.base_icon, count))
            self.tray.setToolTip(f"{APP_NAME}: {count} unread" if count else APP_NAME)
        # Badge on the task manager / dock icon.
        msg = QDBusMessage.createSignal(f"/{APP_CMD}", "com.canonical.Unity.LauncherEntry", "Update")
        msg.setArguments([f"application://{APP_ID}.desktop", {"count": count, "count-visible": count > 0}])
        QDBusConnection.sessionBus().send(msg)

    def on_download(self, download: QWebEngineDownloadRequest):
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        download.setDownloadDirectory(folder)
        download.accept()

        def finished():
            if download.state() == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                self.notifier.notify("Download finished", download.downloadFileName())

        download.isFinishedChanged.connect(finished)

    @pyqtSlot(QDBusMessage)
    def on_ActionInvoked(self, msg):
        nid, _action = msg.arguments()
        notification = self.notifier.active.get(int(nid))
        if notification is not None:
            self.show_window()
            notification.click()

    @pyqtSlot(QDBusMessage)
    def on_NotificationClosed(self, msg):
        self.notifier.active.pop(int(msg.arguments()[0]), None)

    # --- window ------------------------------------------------------------

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visible()

    def toggle_visible(self):
        if self.isVisible() and self.isActiveWindow():
            self.hide()
        else:
            self.show_window()

    def show_window(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def zoom(self, step):
        factor = 1.0 if step is None else min(3.0, max(0.5, self.view.zoomFactor() + step))
        self.view.setZoomFactor(factor)
        self.settings.setValue("zoom", factor)

    def set_autostart(self, on):
        if IN_FLATPAK:
            # The sandbox can't write ~/.config/autostart, so ask the Background portal.
            msg = QDBusMessage.createMethodCall("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                                                "org.freedesktop.portal.Background", "RequestBackground")
            msg.setArguments(["", {
                "reason": f"Start {APP_NAME} at login",
                "autostart": on,
                "commandline": QDBusArgument([APP_CMD, "--hidden"], QMetaType.Type.QStringList.value),
            }])
            QDBusConnection.sessionBus().asyncCall(msg)
            self.settings.setValue("autostart", on)
        elif on:
            AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
            AUTOSTART_FILE.write_text(
                "[Desktop Entry]\nType=Application\n"
                f'Name={APP_NAME}\nIcon={APP_ID}\nExec="{sys.executable}" "{Path(__file__).resolve()}" --hidden\n'
                "X-GNOME-Autostart-enabled=true\n")
        else:
            AUTOSTART_FILE.unlink(missing_ok=True)

    def closeEvent(self, event):
        self.settings.setValue("geometry", self.saveGeometry())
        if not self.quitting and self.tray and self.close_to_tray_action.isChecked():
            event.ignore()
            self.hide()
            return
        event.accept()
        self.quit()

    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        self.settings.setValue("geometry", self.saveGeometry())
        self.hide()
        if self.tray:
            self.tray.hide()
        # The page must go away before its profile does.
        self.page.deleteLater()
        QTimer.singleShot(200, QApplication.instance().quit)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_CMD)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(app_icon())

    # One instance only: a second launch just brings the window up.
    probe = QLocalSocket()
    probe.connectToServer(SOCKET_NAME)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(SOCKET_NAME)
    server = QLocalServer(app)
    server.listen(SOCKET_NAME)

    window = MainWindow(start_hidden="--hidden" in sys.argv)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), window.show_window()))
    app.setQuitOnLastWindowClosed(window.tray is None)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
