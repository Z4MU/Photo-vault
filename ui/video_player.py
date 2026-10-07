"""
PhotoVault - ui/video_player.py
Reproductor de video integrado (QtMultimedia). Si QtMultimedia no está o el
códec no es compatible, ofrece abrir el video con la app del sistema.
"""

import logging

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ui import system

logger = logging.getLogger(__name__)

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget

    MULTIMEDIA_AVAILABLE = True
except ImportError:  # pragma: no cover - depende de la instalación
    MULTIMEDIA_AVAILABLE = False
    logger.warning("QtMultimedia no disponible: los videos se abrirán con la app del sistema")


def format_ms(ms: int) -> str:
    s = max(0, ms) // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


_BTN = (
    "QPushButton{background:#1E1E2E;color:#D0D0E8;border:1px solid #3A3A5A;"
    "border-radius:6px;padding:4px 10px;font-size:13px;}"
    "QPushButton:hover{border-color:#4A9EFF;}"
)


class VideoPlayer(QWidget):
    """Video + controles (reproducir/pausa, posición, tiempo, silencio)."""

    playback_failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path: str | None = None
        self._seeking = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._stack = QStackedLayout()
        holder = QWidget()
        holder.setLayout(self._stack)
        lay.addWidget(holder, stretch=1)

        self._message = QLabel("")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        self._message.setStyleSheet("color:#8888AA;font-size:14px;")

        controls = QHBoxLayout()
        controls.setContentsMargins(8, 0, 8, 6)
        self.btn_play = QPushButton("⏸")
        self.btn_play.setToolTip("Reproducir / pausa (Espacio)")
        self.btn_play.setStyleSheet(_BTN)
        self.btn_play.clicked.connect(self.toggle_play)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.time_lbl = QLabel("0:00 / 0:00")
        self.time_lbl.setStyleSheet("color:#8888AA;font-size:11px;")
        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setToolTip("Silenciar (M)")
        self.btn_mute.setStyleSheet(_BTN)
        self.btn_mute.clicked.connect(self.toggle_mute)
        self.btn_external = QPushButton("↗ Abrir con…")
        self.btn_external.setToolTip("Abrir con la app predeterminada del sistema")
        self.btn_external.setStyleSheet(_BTN)
        self.btn_external.clicked.connect(self._open_external)
        controls.addWidget(self.btn_play)
        controls.addWidget(self.slider, stretch=1)
        controls.addWidget(self.time_lbl)
        controls.addWidget(self.btn_mute)
        controls.addWidget(self.btn_external)
        lay.addLayout(controls)

        self.player = None
        self.audio = None
        if MULTIMEDIA_AVAILABLE:
            self.video_widget = QVideoWidget()
            self.video_widget.setStyleSheet("background:black;")
            self._stack.addWidget(self.video_widget)
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.player.setVideoOutput(self.video_widget)
            self.player.positionChanged.connect(self._on_position)
            self.player.durationChanged.connect(self._on_duration)
            self.player.playbackStateChanged.connect(self._on_state)
            self.player.errorOccurred.connect(self._on_error)
        self._stack.addWidget(self._message)
        if not MULTIMEDIA_AVAILABLE:
            self._show_message("El reproductor integrado no está disponible.")

    # ── API ───────────────────────────────────────────────────────────────────

    def load(self, path: str, autoplay: bool = True) -> None:
        self._path = path
        self.slider.setValue(0)
        self.time_lbl.setText("0:00 / 0:00")
        if self.player is None:
            return
        self._stack.setCurrentIndex(0)
        self.player.setSource(QUrl.fromLocalFile(path))
        if autoplay:
            self.player.play()

    def stop(self) -> None:
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())

    def toggle_play(self) -> None:
        if self.player is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def toggle_mute(self) -> None:
        if self.audio is not None:
            self.audio.setMuted(not self.audio.isMuted())
            self.btn_mute.setText("🔇" if self.audio.isMuted() else "🔊")

    def seek_relative(self, ms: int) -> None:
        if self.player is not None:
            self.player.setPosition(max(0, min(self.player.duration(), self.player.position() + ms)))

    # ── Señales del reproductor ───────────────────────────────────────────────

    def _show_message(self, text: str) -> None:
        self._message.setText(text)
        self._stack.setCurrentWidget(self._message)

    def _on_position(self, pos: int) -> None:
        if not self._seeking:
            self.slider.setValue(pos)
        dur = self.player.duration() if self.player is not None else 0
        self.time_lbl.setText(f"{format_ms(pos)} / {format_ms(dur)}")

    def _on_duration(self, dur: int) -> None:
        self.slider.setRange(0, max(0, dur))

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.btn_play.setText("⏸" if playing else "▶")

    def _on_slider_pressed(self) -> None:
        self._seeking = True

    def _on_slider_released(self) -> None:
        self._seeking = False
        if self.player is not None:
            self.player.setPosition(self.slider.value())

    def _on_error(self, _error, message: str = "") -> None:
        logger.warning("No se pudo reproducir %s: %s", self._path, message)
        self._show_message(
            "No se puede reproducir este video aquí (¿códec no compatible?).\n"
            "Usa «↗ Abrir con…» para verlo con otra app."
        )
        self.playback_failed.emit(message)

    def _open_external(self) -> None:
        if self._path:
            if self.player is not None:
                self.player.pause()
            system.open_external(self._path)
