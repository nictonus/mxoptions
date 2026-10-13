"""The click heard when the wheel changes between ratchet and free spin.

The clicks are synthesised (struck resonances over a burst of noise), so their tone and length can
be set; a file of the user's own can be used instead."""

import cmath
import hashlib
import io
import logging
import math
import os
import random
import shutil
import struct
import subprocess
import wave

from PySide6.QtCore import QObject, QUrl

from .. import paths

try:
    from PySide6.QtMultimedia import QSoundEffect
except ImportError:  # then a command-line player has to do
    QSoundEffect = None

log = logging.getLogger(__name__)

RATE = 48000
CACHE_DIR = os.path.join(paths.CACHE, "sounds")
KINDS = ("freespin", "ratchet")  # what the wheel has just changed to
_PLAYERS = ("pw-play", "paplay", "aplay")

# style -> strikes [(delay s, strength)], resonances [(Hz, amplitude, decay s)], noise (amplitude, decay s)
_RECIPES = {
    # one dry tick
    "tick": ([(0.0, 1.0)], [(2600, 0.55, 0.0030), (4100, 0.30, 0.0020), (1100, 0.45, 0.0050)], (0.40, 0.0007)),
    # one fuller clack, with some body
    "clack": ([(0.0, 1.0)], [(1350, 0.55, 0.0080), (2450, 0.40, 0.0050), (3700, 0.20, 0.0030), (430, 0.70, 0.0140)],
              (0.50, 0.0014)),
    # a relay: the armature lands and bounces once
    "relay": ([(0.0, 1.0), (0.011, 0.36)],
              [(1350, 0.55, 0.0090), (2450, 0.40, 0.0055), (3700, 0.22, 0.0035), (430, 0.70, 0.0160), (215, 0.40, 0.0220)],
              (0.55, 0.0015)),
    # a muffled knock, as heard through the shell of the mouse
    "soft": ([(0.0, 1.0)], [(700, 0.60, 0.0100), (1150, 0.35, 0.0060), (240, 0.70, 0.0160)], (0.15, 0.0020)),
}
STYLES = tuple(_RECIPES)


def synthesize(style, kind, pitch=1.0, length=1.0):
    """One click as the bytes of a WAV file. The ratchet catching sounds a little higher and tighter
    than the wheel letting go, as the two do in a real mechanism."""
    strikes, modes, (noise_level, noise_decay) = _RECIPES[style if style in _RECIPES else STYLES[0]]
    if kind == "ratchet":
        pitch, length = pitch * 1.22, length * 0.85
    rng = random.Random(f"{style}/{kind}")
    lead = int(0.001 * RATE)
    longest = max(decay for _f, _a, decay in modes) * length
    count = lead + int((strikes[-1][0] + 9.0 * longest) * RATE)
    samples = [0.0] * count
    for delay, strength in strikes:
        first = lead + int(delay * RATE)
        for freq, amplitude, decay in modes:
            decay *= length
            turn = cmath.exp(complex(-1.0 / (decay * RATE), 2.0 * math.pi * freq * pitch / RATE))
            value = cmath.rect(strength * amplitude, rng.uniform(0.0, 2.0 * math.pi))
            for i in range(first, min(count, first + int(9.0 * decay * RATE))):
                samples[i] += value.imag
                value *= turn
        decay, previous = noise_decay * length, 0.0
        fade = math.exp(-1.0 / (decay * RATE))
        level = strength * noise_level
        for i in range(first, min(count, first + int(9.0 * decay * RATE))):
            noise = rng.gauss(0.0, 1.0)
            samples[i] += level * (0.6 * noise + 0.4 * (noise - previous))  # tilted towards the highs
            previous, level = noise, level * fade
    # no step at the very start, no step at the end, and a soft ceiling so a short click still carries
    attack, tail = int(0.0003 * RATE), int(0.004 * RATE)
    for i in range(attack):
        samples[lead + i] *= i / attack
    for i in range(tail):
        samples[count - 1 - i] *= i / tail
    peak = max(1e-9, max(abs(s) for s in samples))
    shaped = [math.tanh(1.8 * s / peak) for s in samples]
    peak = max(1e-9, max(abs(s) for s in shaped))
    data = struct.pack(f"<{count}h", *(int(s / peak * 0.9 * 32767) for s in shaped))
    out = io.BytesIO()
    with wave.open(out, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(data)
    return out.getvalue()


def sound_path(settings, kind):
    """File to play for ``kind`` with the "wheel_click" settings, made on first use; empty if there is none."""
    if settings["sound"] == "custom":
        return settings["file"] if settings["file"] and os.path.isfile(settings["file"]) else ""
    key = f"{settings['sound']}-{kind}-{settings['pitch']:.2f}-{settings['length']:.2f}-v2"
    path = os.path.join(CACHE_DIR, hashlib.sha1(key.encode()).hexdigest()[:16] + ".wav")
    if not os.path.exists(path):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(path + ".tmp", "wb") as f:
                f.write(synthesize(settings["sound"], kind, settings["pitch"], settings["length"]))
            os.replace(path + ".tmp", path)
            # dragging the tone and length sliders leaves a file per position behind: keep the newest few
            files = sorted((os.path.join(CACHE_DIR, name) for name in os.listdir(CACHE_DIR) if name.endswith(".wav")),
                           key=os.path.getmtime)
            for stale in files[:-40]:
                os.remove(stale)
        except OSError as e:
            log.warning("could not write %s: %s", path, e)
            return ""
    return path


def loudness(volume):
    """Amplitude for a volume setting: squared, so the lower half of the slider is the quiet half."""
    return max(0.0, min(1.0, volume)) ** 2


class WheelSounds(QObject):
    """Keeps the two clicks loaded so they start without a delay."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = None
        self._effects = {}  # kind -> QSoundEffect
        self._paths = {}
        self._volume = 0.0
        self._player = next((path for path in map(shutil.which, _PLAYERS) if path), None)

    def configure(self, settings):
        """Take the "wheel_click" section of the configuration."""
        wanted = tuple(settings[key] for key in ("sound", "file", "pitch", "length", "volume"))
        if wanted == self._settings:
            return
        self._settings = wanted
        self._volume = loudness(settings["volume"])
        self._paths = {kind: sound_path(settings, kind) for kind in KINDS}
        if QSoundEffect is None:
            return
        for kind, path in self._paths.items():
            effect = self._effects.get(kind)
            if effect is None:
                effect = self._effects[kind] = QSoundEffect(self)
            url = QUrl.fromLocalFile(path) if path else QUrl()
            if effect.source() != url:
                effect.setSource(url)
            effect.setVolume(self._volume)

    def play(self, kind):
        """``kind`` is "freespin" or "ratchet"."""
        path = self._paths.get(kind)
        if not path:
            return
        effect = self._effects.get(kind)
        if effect is not None and effect.status() != QSoundEffect.Status.Error:
            effect.play()
        elif self._player:
            volume = {"pw-play": ["--volume", f"{self._volume:.3f}"],
                      "paplay": [f"--volume={int(self._volume * 65536)}"]}.get(os.path.basename(self._player), [])
            try:
                subprocess.Popen([self._player, *volume, path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError as e:
                log.debug("could not play %s: %s", path, e)
