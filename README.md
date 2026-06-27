# piphone

Raspberry Pi 5 + SIM7600G-H phone. Flask backend with dummy modem stub —
swap in the real AT driver when the hardware is connected.

## Setup

```bash
cd ~/piphone
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Backend runs on http://0.0.0.0:5000

## Kiosk (X11/VNC for now, MHS35 later)

```bash
# on the Pi, inside your X session / VNC:
chromium-browser \
  --kiosk \
  --window-size=320,480 \
  --window-position=0,0 \
  --disable-infobars \
  --noerrdialogs \
  --disable-session-crashed-bubble \
  --disable-restore-session-state \
  http://localhost:5000
```

## systemd (optional)

```bash
sudo cp piphone.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now piphone
```

## Swap stub → real modem

1. Implement `modem/at.py` with class `ModemDriver` matching the same
   method signatures as `ModemStub` in `modem/stub.py`.
2. In `app.py`, change:
   ```python
   from modem.stub import ModemStub
   modem = ModemStub()
   ```
   to:
   ```python
   from modem.at import ModemDriver
   modem = ModemDriver(port="/dev/ttyUSB2", baudrate=115200)
   ```
3. Restart the service.

## API reference

| Method | Endpoint              | Body / Notes                        |
|--------|-----------------------|-------------------------------------|
| GET    | /api/sms/inbox        | Returns thread list                 |
| POST   | /api/sms/send         | `{number, text}`                    |
| POST   | /api/sms/delete       | `{id}`                              |
| POST   | /api/call/dial        | `{number}`                          |
| POST   | /api/call/hangup      | —                                   |
| POST   | /api/call/answer      | —                                   |
| GET    | /api/call/status      | Returns call state + duration       |
| GET    | /api/modem/status     | Signal, operator, RAT, IMEI         |
| POST   | /api/modem/ussd       | `{code}` e.g. `"*100#"`             |
| GET    | /api/contacts         | Local JSON contacts store           |
| POST   | /api/contacts/save    | Full contacts array                 |
| GET    | /api/events           | SSE stream (sms / ring / delivered) |

## Modem port map (SIM7600G-H via USB)

| Port       | Function          |
|------------|-------------------|
| /dev/ttyUSB0 | Diagnostic      |
| /dev/ttyUSB1 | GPS NMEA        |
| /dev/ttyUSB2 | AT commands ← use this |
| /dev/ttyUSB3 | Modem (PPP)     |
