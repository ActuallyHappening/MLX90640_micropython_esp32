import array
import time
import subprocess
import os, signal
import traceback
import signal
import sysv_ipc
import atexit
import random

DEBUG = False
queues = []
procs = []

# The message queues live outside of python space, and must be formally cleaned!
def final():
    """In case the program is cancelled or quit, we need to clean up the PulseIn
    helper process and also the message queue, this is called at exit to do so"""
    if DEBUG:
        print("Cleaning up message queues", queues)
        print("Cleaning up processes", procs)
    for q in queues:
        q.remove()
    for proc in procs:
        proc.terminate()
atexit.register(final)

class PulseIn:
    def __init__(self, pin, maxlen=2, idle_state=False):
        """Create a PulseIn object associated with the given pin.
        The object acts as a read-only sequence of pulse lengths with
        a given max length. When it is active, new pulse lengths are
        added to the end of the list. When there is no more room
        (len() == maxlen) the oldest pulse length is removed to make room."""
        self._pin = pin
        self._maxlen = maxlen
        self._idle_state = idle_state
        self._queue_key = random.randint(1, 9999)
        try:
            self._mq = sysv_ipc.MessageQueue(None, flags=sysv_ipc.IPC_CREX)
            if DEBUG:
                print("Message Queue Key: ", self._mq.key)
            queues.append(self._mq)
        except sysv_ipc.ExistentialError:
            raise RuntimeError("Message queue creation failed")

        dir_path = os.path.dirname(os.path.realpath(__file__))
        cmd = [dir_path+"/libgpiod_pulsein",
               "--pulses", str(maxlen),
               "--queue", str(self._mq.key)]
        if idle_state:
            cmd.append("-i")
        cmd.append("gpiochip0")
        cmd.append(str(pin))
        if DEBUG:
            print(cmd)
        
        self._process = subprocess.Popen(cmd)
        procs.append(self._process)

        # wait for it to start up
        if DEBUG:
            print("Waiting for startup success message from subprocess")
        message = self._wait_receive_msg()
        if message[0] != b'!':
            raise RuntimeError("Could not establish message queue with subprocess")
        self._paused = False

    def _wait_receive_msg(self, timeout=0.25, type=2):
        """Internal helper that will wait for new messages of a given type,
        and throw an exception on timeout"""
        stamp = time.monotonic()
        while (time.monotonic() - stamp) < timeout:
            try:
                message = self._mq.receive(block=False, type=2)
                return message
            except sysv_ipc.BusyError:
                time.sleep(0.001) # wait a bit then retry!
        # uh-oh timed out
        raise RuntimeError("Timed out waiting for PulseIn message")

    def deinit(self):
        """Deinitialises the PulseIn and releases any hardware and software
        resources for reuse."""
        # Clean up after ourselves
        self._process.terminate()
        procs.remove(self._process)
        self._mq.remove()
        queues.remove(self._mq)

    def __enter__(self):
        """No-op used by Context Managers."""
        return self

    def __exit__(self, exc_type, exc_value, tb):
        """Automatically deinitializes the hardware when exiting a context."""
        self.deinit()

    def resume(self, trigger_duration=0):
        """Resumes pulse capture after an optional trigger pulse."""
        if trigger_duration != 0:
            self._mq.send("t%d" % trigger_duration, True, type=1)
        else:
            self._mq.send("r", True, type=1)
        self._paused = False

    def pause(self):
        """Pause pulse capture"""
        self._mq.send("p", True, type=1)
        self._paused = True

    @property
    def paused(self):
        """True when pulse capture is paused as a result of pause() or
        an error during capture such as a signal that is too fast."""
        return self._paused

    @property
    def maxlen(self):
        """The maximum length of the PulseIn. When len() is equal to maxlen,
        it is unclear which pulses are active and which are idle."""
        return self._maxlen

    def clear(self):
        """Clears all captured pulses"""
        self._mq.send("c", True, type=1)

    def popleft(self):
        """Removes and returns the oldest read pulse."""
        self._mq.send("^", True, type=1)
        message = self._wait_receive_msg()
        reply = int(message[0].decode('utf-8'))
        #print(reply)
        if reply == -1:
            raise IndexError("pop from empty list")
        return reply

    def __len__(self):
        """Returns the current pulse length"""
        self._mq.send("l", True, type=1)
        message = self._wait_receive_msg()
        return int(message[0].decode('utf-8'))

    def __getitem__(self, index, type=None):
        """Returns the value at the given index or values in slice."""
        self._mq.send("i%d" % index, True, type=1)
        message = self._wait_receive_msg()
        ret = int(message[0].decode('utf-8'))
        if ret == -1:
            raise IndexError("list index out of range")
        return ret
