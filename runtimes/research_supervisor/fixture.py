"""Networkless N1 fixture: controller exit does not terminate the pool member.

This is not RD-Agent or OpenEvolve. Engine activation supplies a new registered
profile/image and tests its actual upstream loop separately.
"""

import json
import multiprocessing
import os
import socket
import time
from multiprocessing import shared_memory


def controller():
    return


def pool_member():
    time.sleep(600)


if __name__ == "__main__":
    context = multiprocessing.get_context("spawn")
    shared = shared_memory.SharedMemory(create=True, size=64)
    worker = context.Process(target=pool_member)
    controller_process = context.Process(target=controller)
    worker.start()
    controller_process.start()
    controller_process.join(5)
    with open("/ipc/ticket.json") as stream:
        ticket = json.load(stream)["ticket"]
    for _ in range(100):
        try:
            with socket.socket(socket.AF_UNIX) as peer:
                peer.connect("/ipc/peer.sock")
                peer.sendall(
                    json.dumps(
                        {
                            "ticket": ticket,
                            "controller_exited": controller_process.exitcode == 0,
                            "pool_alive": worker.is_alive(),
                            "pid": os.getpid(),
                        }
                    ).encode()
                    + b"\n"
                )
                break
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(0.1)
    worker.join(600)
    shared.close()
    shared.unlink()
