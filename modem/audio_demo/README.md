# Audio bridge demo

Standalone test of the SIM7600 USB Audio interface, before wiring
into the main app. Validates:

1. Claiming the unclaimed USB interface (bulk endpoints for PCM)
2. AT+CPCMREG=1/0 control via the existing AT port
3. Reading PCM from modem -> playing on USB sound card
4. Reading mic from USB sound card -> writing PCM to modem

## Usage

```bash
# 1. find your USB audio card index
aplay -l

# 2. dial a call manually via debug CLI or the phone app, then run:
python3 test_audio_bridge.py --card 2

# 3. answer/wait for the call to connect, audio should start flowing
```

## Requires

```bash
pip install pyusb pyaudio
apt install portaudio19-dev -y   # pyaudio build dependency
```
