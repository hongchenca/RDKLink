import os
import platform
import socket
import sys
import time

print("=== RDKLINK REAL X5 TEST ===", flush=True)
print("HOSTNAME:", socket.gethostname(), flush=True)
print("ARCH:", platform.machine(), flush=True)
print("PYTHON:", sys.version, flush=True)
print("CWD:", os.getcwd(), flush=True)

print("STDERR_TEST_OK", file=sys.stderr, flush=True)

for i in range(5):
    print(f"TICK {i}", flush=True)
    time.sleep(1)

print("TEST_FINISHED", flush=True)
