import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from urllib.parse import urlparse
import requests

from .utils import safe_filename, get_file_hash


class DownloadManager:
    """Multi-threaded image download manager with pause/resume/cancel support."""

    def __init__(self, max_workers=4):
        self._executor = None
        self._max_workers = max_workers
        self._futures = {}       # url -> Future
        self._pause_event = threading.Event()
        self._pause_event.set()  # start in running state
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._active = False

        # Callbacks (set by GUI)
        self.on_progress = None   # (completed, total, current_url)
        self.on_file_done = None  # (url, filepath)
        self.on_error = None      # (url, error_msg)
        self.on_all_done = None   # ()
        self.on_status = None     # (msg)

        # Stats
        self.completed = 0
        self.total = 0
        self.errors = []

    def start(self, urls, save_dir, headers, dedup_by_md5=False, known_hashes=None):
        """Begin downloading a list of URLs."""
        if self._active:
            return False

        self._active = True
        self._cancel_event.clear()
        self._pause_event.set()
        self.completed = 0
        self.total = len(urls)
        self.errors = []

        if known_hashes is None:
            known_hashes = set()

        os.makedirs(save_dir, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers)

        for url in urls:
            if self._cancel_event.is_set():
                break
            future = self._executor.submit(
                self._download_one, url, save_dir, headers, dedup_by_md5, known_hashes
            )
            with self._lock:
                self._futures[url] = future

        return True

    def _download_one(self, url, save_dir, headers, dedup_by_md5, known_hashes):
        """Download a single image (runs in worker thread)."""
        if self._cancel_event.is_set():
            return

        # Respect pause
        self._pause_event.wait()
        if self._cancel_event.is_set():
            return

        filename = safe_filename(url)
        filepath = os.path.join(save_dir, filename)

        try:
            response = requests.get(url, headers=headers, stream=True, timeout=60)
            response.raise_for_status()

            # Write to temp file, then rename
            tmp_path = filepath + '.tmp'
            with open(tmp_path, 'wb') as f:
                for chunk in response.iter_content(8192):
                    if self._cancel_event.is_set():
                        f.close()
                        os.remove(tmp_path)
                        return
                    self._pause_event.wait()
                    if self._cancel_event.is_set():
                        f.close()
                        os.remove(tmp_path)
                        return
                    f.write(chunk)

            # Check for duplicates by MD5
            if dedup_by_md5:
                fhash = get_file_hash(tmp_path)
                if fhash and fhash in known_hashes:
                    os.remove(tmp_path)
                    self._emit_status(f"跳过重复图片: {filename}")
                    with self._lock:
                        self.completed += 1
                    self._emit_progress()
                    return
                if fhash:
                    known_hashes.add(fhash)

            # Finalize
            os.replace(tmp_path, filepath)
            with self._lock:
                self.completed += 1
            self._emit_progress()
            self._emit_file_done(url, filepath)
            self._emit_status(f"下载完成: {filename}")

        except Exception as e:
            with self._lock:
                self.completed += 1
                self.errors.append((url, str(e)))
            self._emit_progress()
            self._emit_error(url, str(e))
            self._emit_status(f"下载失败: {filename} — {e}")

        # Check if all done
        with self._lock:
            if self.completed >= self.total:
                self._active = False
                self._emit_all_done()

    def pause(self):
        self._pause_event.clear()
        self._emit_status("已暂停")

    def resume(self):
        self._pause_event.set()
        self._emit_status("已恢复")

    def cancel(self):
        self._cancel_event.set()
        self._pause_event.set()  # Unpause so threads can check cancel flag
        self._active = False
        self._emit_status("已取消")
        if self._executor:
            self._executor.shutdown(wait=False)

    @property
    def is_paused(self):
        return not self._pause_event.is_set()

    @property
    def is_active(self):
        return self._active

    # --- Callback emitters (thread-safe) ---

    def _emit_progress(self):
        if self.on_progress:
            self.on_progress(self.completed, self.total)

    def _emit_file_done(self, url, filepath):
        if self.on_file_done:
            self.on_file_done(url, filepath)

    def _emit_error(self, url, msg):
        if self.on_error:
            self.on_error(url, msg)

    def _emit_all_done(self):
        if self.on_all_done:
            self.on_all_done()

    def _emit_status(self, msg):
        if self.on_status:
            self.on_status(msg)

    def shutdown(self):
        """Clean shutdown — wait for running tasks to finish."""
        self.cancel()
        if self._executor:
            self._executor.shutdown(wait=True)
