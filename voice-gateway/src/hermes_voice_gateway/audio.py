from __future__ import annotations

import queue
import threading
from collections.abc import Callable

import pyaudio


class PCMPlayer:
    def __init__(self, device_index: int | None = None, sample_rate: int = 24000) -> None:
        self._pa = pyaudio.PyAudio()
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._stream = self._pa.open(format=pyaudio.paInt16, channels=1, rate=sample_rate,
                                     output=True, output_device_index=device_index, frames_per_buffer=2400)
        self._playing = threading.Event()
        self._thread = threading.Thread(target=self._worker, name="qwen-pcm-player", daemon=True)
        self._thread.start()

    @property
    def is_playing(self) -> bool:
        return self._playing.is_set() or not self._queue.empty()

    def add(self, data: bytes) -> None:
        self._queue.put(data)

    def clear(self) -> None:
        with self._queue.mutex:
            self._queue.queue.clear()
        self._playing.clear()

    def _worker(self) -> None:
        while True:
            data = self._queue.get()
            if data is None:
                return
            self._playing.set()
            try:
                self._stream.write(data)
            finally:
                self._playing.clear()

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=2)
        self._stream.stop_stream()
        self._stream.close()
        self._pa.terminate()


class PCMCapture:
    def __init__(self, device_index: int | None = None, sample_rate: int = 16000, chunk_ms: int = 100) -> None:
        self._pa = pyaudio.PyAudio()
        self.frames_per_chunk = max(160, int(sample_rate * chunk_ms / 1000))
        self._stream = self._pa.open(format=pyaudio.paInt16, channels=1, rate=sample_rate,
                                     input=True, input_device_index=device_index,
                                     frames_per_buffer=self.frames_per_chunk)

    def read(self) -> bytes:
        return self._stream.read(self.frames_per_chunk, exception_on_overflow=False)

    def close(self) -> None:
        self._stream.stop_stream()
        self._stream.close()
        self._pa.terminate()


def list_audio_devices(printer: Callable[[str], None] = print) -> None:
    pa = pyaudio.PyAudio()
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            printer(f"{i:>3}  in={int(info.get('maxInputChannels', 0)):<2} out={int(info.get('maxOutputChannels', 0)):<2}  {info.get('name', '')}")
    finally:
        pa.terminate()
