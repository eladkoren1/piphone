"""
Quick standalone test — send AT commands and print raw bytes received.
Run directly: python3 debug_serial.py
"""
import serial, time

PORT = "/dev/ttyUSB2"
ser  = serial.Serial(PORT, 115200, timeout=2)
time.sleep(0.3)

def send(cmd):
    print(f"\n→ {cmd!r}")
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    time.sleep(0.5)
    raw = ser.read(512)
    print(f"← raw bytes: {raw!r}")
    print(f"← decoded:   {raw.decode(errors='replace')!r}")

send("AT")
send("ATE0")
send("AT+CSQ")
send("AT+CREG?")

ser.close()
print("\ndone")
