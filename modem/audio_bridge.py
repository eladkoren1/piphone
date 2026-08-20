"""
modem/audio_bridge.py — bridges SIM7600G-H voice call PCM audio to a
USB sound card via ALSA (pyaudio), so calls can be heard/spoken into.

Protocol (per SIMCom USB AUDIO application note):
  1. Call connects -> modem emits "VOICE CALL: BEGIN" URC
  2. We send AT+CPCMREG=1 to start PCM streaming
  3. Raw 8kHz/16-bit mono PCM flows over USB interface 5:
       EP 0x8a (bulk IN)  -- audio FROM modem (remote party's voice)
       EP 0x06 (bulk OUT) -- audio TO modem (local mic)
  4. Call ends -> modem emits "VOICE CALL: END"
  5. We send AT+CPCMREG=0 to stop streaming

This module owns:
  - the raw USB interface 5 (via pyusb, no kernel driver involved)
  - the ALSA USB sound card (via pyaudio) for actual speaker/mic I/O
  - simple linear resampling between the modem's 8kHz and the sound
    card's native rate (typically 44.1kHz or 48kHz)
"""

import logging, threading, time, queue
import numpy as np

log = logging.getLogger("modem.audio")

try:
    import usb.core
    import usb.util
except ImportError:
    usb = None

try:
    import pyaudio
except ImportError:
    pyaudio = None

# SIM7600G-H USB identifiers
VENDOR_ID  = 0x1e0e
PRODUCT_ID = 0x9001
AUDIO_IFACE = 5
EP_IN  = 0x8a   # modem -> host (remote party's voice)
EP_OUT = 0x06   # host -> modem (local mic)

MODEM_RATE = 8000     # Hz, fixed by modem (16000 possible via AT+CPCMFRM=1)
CHUNK_MS   = 20        # audio chunk size in milliseconds
MODEM_CHUNK_SAMPLES = int(MODEM_RATE * CHUNK_MS / 1000)   # 160 samples @ 8k/20ms
MODEM_CHUNK_BYTES   = MODEM_CHUNK_SAMPLES * 2               # 16-bit = 2 bytes/sample


def _find_sound_card_index(pa, name_hint=None):
    """Find a USB sound card device index for pyaudio, preferring
    one with both input and output channels."""
    best = None
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        name = info.get("name", "")
        if name_hint and name_hint.lower() not in name.lower():
            continue
        if info.get("maxInputChannels", 0) > 0 and info.get("maxOutputChannels", 0) > 0:
            return i
        if best is None and (info.get("maxInputChannels", 0) > 0
                             or info.get("maxOutputChannels", 0) > 0):
            best = i
    return best


def _resample_linear(samples: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Simple linear-interpolation resample. Good enough for voice."""
    if from_rate == to_rate or len(samples) == 0:
        return samples
    duration = len(samples) / from_rate
    out_len  = int(duration * to_rate)
    if out_len <= 0:
        return np.array([], dtype=samples.dtype)
    x_old = np.linspace(0, duration, num=len(samples), endpoint=False)
    x_new = np.linspace(0, duration, num=out_len, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(samples.dtype)


class AudioBridge:
    """
    Owns the USB audio interface and the ALSA sound card for the
    duration of a call. Call start()/stop() around VOICE CALL:
    BEGIN/END. Thread-safe start/stop, idempotent.
    """

    def __init__(self, sound_card_hint=None):
        self._sound_card_hint = sound_card_hint
        self._running    = False
        self._lock       = threading.Lock()
        self._usb_dev    = None
        self._pa         = None
        self._pa_out     = None   # speaker stream
        self._pa_in      = None   # mic stream
        self._rx_thread  = None
        self._tx_thread  = None
        self._card_rate  = 48000  # actual sound card sample rate, detected

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> bool:
        with self._lock:
            if self._running:
                log.info("Audio bridge already running")
                return True

            if usb is None:
                log.error("pyusb not installed — cannot start audio bridge")
                return False
            if pyaudio is None:
                log.error("pyaudio not installed — cannot start audio bridge")
                return False

            try:
                self._open_usb()
                self._open_sound_card()
            except Exception as e:
                log.error("Failed to start audio bridge: %s", e)
                self._cleanup()
                return False

            self._running = True
            self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
            self._tx_thread = threading.Thread(target=self._tx_loop, daemon=True)
            self._rx_thread.start()
            self._tx_thread.start()
            log.info("Audio bridge started (card rate=%d Hz)", self._card_rate)
            return True

    def stop(self):
        with self._lock:
            if not self._running:
                return
            self._running = False

        if self._rx_thread:
            self._rx_thread.join(timeout=2)
        if self._tx_thread:
            self._tx_thread.join(timeout=2)

        self._cleanup()
        log.info("Audio bridge stopped")

    def _cleanup(self):
        try:
            if self._pa_out:
                self._pa_out.stop_stream()
                self._pa_out.close()
        except Exception:
            pass
        try:
            if self._pa_in:
                self._pa_in.stop_stream()
                self._pa_in.close()
        except Exception:
            pass
        try:
            if self._pa:
                self._pa.terminate()
        except Exception:
            pass
        try:
            if self._usb_dev is not None:
                usb.util.dispose_resources(self._usb_dev)
        except Exception:
            pass
        self._pa_out = self._pa_in = self._pa = self._usb_dev = None

    # ── USB audio interface ──────────────────────────────────────────────────

    def _open_usb(self):
        dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if dev is None:
            raise RuntimeError("SIM7600 USB device not found")

        # interface 5 has no kernel driver, so no detach needed —
        # but try anyway in case something else claimed it
        try:
            if dev.is_kernel_driver_active(AUDIO_IFACE):
                dev.detach_kernel_driver(AUDIO_IFACE)
        except (NotImplementedError, usb.core.USBError):
            pass

        usb.util.claim_interface(dev, AUDIO_IFACE)
        self._usb_dev = dev
        log.info("Claimed USB audio interface %d", AUDIO_IFACE)

    # ── ALSA sound card ───────────────────────────────────────────────────────

    def _open_sound_card(self):
        self._pa = pyaudio.PyAudio()
        idx = _find_sound_card_index(self._pa, self._sound_card_hint)
        if idx is None:
            raise RuntimeError("No suitable USB sound card found")

        info = self._pa.get_device_info_by_index(idx)
        self._card_rate = int(info.get("defaultSampleRate", 48000))
        log.info("Using sound card [%d] %s @ %d Hz",
                 idx, info.get("name"), self._card_rate)

        self._pa_out = self._pa.open(
            format=pyaudio.paInt16, channels=1, rate=self._card_rate,
            output=True, output_device_index=idx,
            frames_per_buffer=int(self._card_rate * CHUNK_MS / 1000),
        )
        self._pa_in = self._pa.open(
            format=pyaudio.paInt16, channels=1, rate=self._card_rate,
            input=True, input_device_index=idx,
            frames_per_buffer=int(self._card_rate * CHUNK_MS / 1000),
        )

    # ── RX: modem -> speaker ──────────────────────────────────────────────────

    def _rx_loop(self):
        """Read PCM from modem USB EP, resample, play on speaker."""
        while self._running:
            try:
                data = self._usb_dev.read(EP_IN, MODEM_CHUNK_BYTES * 4, timeout=100)
                if not data:
                    continue
                samples = np.frombuffer(bytes(data), dtype=np.int16)
                if self._card_rate != MODEM_RATE:
                    samples = _resample_linear(samples, MODEM_RATE, self._card_rate)
                self._pa_out.write(samples.astype(np.int16).tobytes())
            except usb.core.USBTimeoutError:
                continue
            except usb.core.USBError as e:
                if self._running:
                    log.warning("RX USB error: %s", e)
                time.sleep(0.05)
            except Exception as e:
                if self._running:
                    log.warning("RX loop error: %s", e)
                time.sleep(0.05)

    # ── TX: mic -> modem ──────────────────────────────────────────────────────

    def _tx_loop(self):
        """Read PCM from mic, resample down to 8kHz, send to modem USB EP."""
        chunk_frames = int(self._card_rate * CHUNK_MS / 1000) if self._card_rate else 960
        while self._running:
            try:
                raw = self._pa_in.read(chunk_frames, exception_on_overflow=False)
                samples = np.frombuffer(raw, dtype=np.int16)
                if self._card_rate != MODEM_RATE:
                    samples = _resample_linear(samples, self._card_rate, MODEM_RATE)
                self._usb_dev.write(EP_OUT, samples.astype(np.int16).tobytes(), timeout=100)
            except usb.core.USBTimeoutError:
                continue
            except usb.core.USBError as e:
                if self._running:
                    log.warning("TX USB error: %s", e)
                time.sleep(0.05)
            except Exception as e:
                if self._running:
                    log.warning("TX loop error: %s", e)
                time.sleep(0.05)
