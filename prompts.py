"""
Antrean prompt yang aman untuk banyak worker.

Prinsip penting:
- Prompt yang sudah dipakai langsung DIHAPUS dari file .txt sumber, supaya
  setiap browser mendapat promptnya sendiri dan tidak ada yang terpakai dua kali.
- Salinan lengkap (teks + status) tetap dicatat ke folder state/run_<tanggal>/.
- Setiap prompt punya status: pending / running / done / failed.
- Kalau bot dihentikan, prompt yang sedang diproses dikembalikan ke pending.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import config

logger = logging.getLogger(__name__)

STATE_DIR = config.STATE_DIR


# ==============================================================================
# PEMBACA FILE
# ==============================================================================
def read_prompt_blocks(prompt_file: str | os.PathLike) -> list[str]:
    """Baca prompt dari file. Satu prompt = satu blok paragraf (dipisah baris kosong)."""
    path = Path(prompt_file)
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8-sig")
    except Exception as e:
        logger.error(f"Gagal membaca file prompt {path}: {e}")
        return []

    blocks = [block.strip() for block in content.replace("\r\n", "\n").split("\n\n")]
    return [b for b in blocks if b]


def count_prompts(prompt_file: str | os.PathLike) -> int:
    """Hitung jumlah prompt di sebuah file (untuk ditampilkan di UI)."""
    return len(read_prompt_blocks(prompt_file))


# ==============================================================================
# MODEL ITEM
# ==============================================================================
@dataclass
class PromptItem:
    index: int
    text: str
    status: str = "pending"          # pending | running | done | failed
    attempts: int = 0
    browser: int | None = None
    reason: str = ""
    finished_at: str = ""


# ==============================================================================
# QUEUE
# ==============================================================================
class PromptQueue:
    """Antrean prompt thread-safe dengan pencatatan ke disk."""

    def __init__(self, source_file: str | os.PathLike, max_attempts: int = 3):
        self.source_file = str(source_file)
        self.max_attempts = max_attempts
        self.items: list[PromptItem] = [
            PromptItem(index=i, text=text)
            for i, text in enumerate(read_prompt_blocks(source_file))
        ]
        self._lock = threading.Lock()
        self._run_dir = self._create_run_dir()
        self._save()

    # --------------------------------------------------------------------------
    # Direktori hasil
    # --------------------------------------------------------------------------
    @staticmethod
    def _create_run_dir() -> Path:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        run_dir = STATE_DIR / f"run_{stamp}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    def _save(self) -> None:
        """Simpan status antrean (dipanggil dengan lock aktif)."""
        try:
            payload = {
                "source_file": self.source_file,
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "items": [item.__dict__ for item in self.items],
            }
            self._run_dir.joinpath("queue.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # Arsip teks agar mudah dibaca manusia
            done = [i.text for i in self.items if i.status == "done"]
            failed = [i.text for i in self.items if i.status == "failed"]
            self._run_dir.joinpath("done.txt").write_text(
                "\n\n".join(done), encoding="utf-8"
            )
            self._run_dir.joinpath("failed.txt").write_text(
                "\n\n".join(failed), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"Gagal menyimpan state antrean: {e}")

    # --------------------------------------------------------------------------
    # Operasi antrean
    # --------------------------------------------------------------------------
    def take(self, browser: int) -> PromptItem | None:
        """Ambil prompt pending berikutnya dan tandai running."""
        with self._lock:
            for item in self.items:
                if item.status == "pending":
                    item.status = "running"
                    item.browser = browser
                    item.attempts += 1
                    item.reason = ""
                    # Prompt yang sudah dipakai langsung dibuang dari file
                    # sumber, supaya tiap browser mendapat bagiannya sendiri
                    # dan tidak ada prompt yang terpakai dua kali saat restart.
                    self._remove_from_source(item.text)
                    self._save()
                    logger.info(
                        f"✂️  Prompt #{item.index + 1} diambil oleh Browser {browser} "
                        f"(percobaan {item.attempts}/{self.max_attempts})"
                    )
                    return item
        return None

    def _remove_from_source(self, text: str) -> None:
        """Hapus satu blok prompt dari file sumber (dipanggil dengan lock aktif)."""
        path = Path(self.source_file)
        try:
            if not path.exists():
                return
            raw = path.read_text(encoding="utf-8-sig")
        except Exception as e:
            logger.warning(f"Tidak bisa membaca file prompt untuk dipangkas: {e}")
            return

        newline = "\r\n" if "\r\n" in raw else "\n"
        blocks = raw.replace("\r\n", "\n").split("\n\n")
        target = text.strip()
        for i, block in enumerate(blocks):
            if block.strip() == target:
                del blocks[i]
                break
        else:
            return  # tidak ada yang perlu dihapus

        # Rapikan: buang blok kosong berlebih di awal/akhir.
        while blocks and not blocks[0].strip():
            blocks.pop(0)
        while blocks and not blocks[-1].strip():
            blocks.pop()

        content = "\n\n".join(blocks)
        if content:
            content += "\n"

        try:
            # Tulis atomik: file sementara di folder yang sama, lalu ganti.
            tmp_path = path.with_name(path.name + ".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            os.replace(tmp_path, path)
            remaining = len([b for b in blocks if b.strip()])
            logger.info(
                f"🗑️  Prompt dipangkas dari {path.name} — sisa {remaining} prompt."
            )
        except Exception as e:
            logger.warning(f"Gagal memangkas file prompt: {e}")

    def complete(self, item: PromptItem) -> None:
        with self._lock:
            item.status = "done"
            item.reason = ""
            item.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save()
            logger.info(f"✅ Prompt #{item.index + 1} selesai.")

    def requeue(self, item: PromptItem, reason: str = "") -> None:
        """Kembalikan prompt ke antrean, atau tandai gagal bila percobaan habis."""
        with self._lock:
            item.reason = reason
            item.browser = None
            if item.attempts >= self.max_attempts:
                item.status = "failed"
                item.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
                logger.error(
                    f"❌ Prompt #{item.index + 1} GAGAL setelah "
                    f"{item.attempts} percobaan: {reason}"
                )
            else:
                item.status = "pending"
                logger.warning(
                    f"↩️  Prompt #{item.index + 1} dikembalikan ke antrean: {reason}"
                )
            self._save()

    def fail(self, item: PromptItem, reason: str) -> None:
        with self._lock:
            item.status = "failed"
            item.reason = reason
            item.browser = None
            item.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save()
            logger.error(f"❌ Prompt #{item.index + 1} gagal: {reason}")

    def release_running(self) -> None:
        """Saat bot dihentikan: semua prompt 'running' dikembalikan ke pending."""
        with self._lock:
            changed = False
            for item in self.items:
                if item.status == "running":
                    item.status = "pending"
                    item.browser = None
                    changed = True
            if changed:
                self._save()
                logger.info("↩️  Prompt yang sedang diproses dikembalikan ke antrean.")

    # --------------------------------------------------------------------------
    # Statistik
    # --------------------------------------------------------------------------
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "total": len(self.items),
                "pending": sum(1 for i in self.items if i.status == "pending"),
                "running": sum(1 for i in self.items if i.status == "running"),
                "done": sum(1 for i in self.items if i.status == "done"),
                "failed": sum(1 for i in self.items if i.status == "failed"),
            }

    def empty(self) -> bool:
        with self._lock:
            return not any(i.status == "pending" for i in self.items)

    def remaining_prompts(self) -> int:
        """Jumlah prompt yang masih menunggu dikerjakan (belum diambil)."""
        with self._lock:
            return sum(1 for i in self.items if i.status == "pending")

    def has_work(self) -> bool:
        """
        Masih ada prompt yang pending atau sedang dikerjakan browser lain?

        Dipakai worker yang sudah kehabisan prompt: ia menunggu sebentar
        (state idle) supaya bisa mengambil prompt yang di-requeue browser lain,
        bukan langsung berhenti.
        """
        with self._lock:
            return any(i.status in ("pending", "running") for i in self.items)

    def assignments(self) -> dict[int, list[int]]:
        """Peta Browser -> daftar nomor prompt (1-based) yang pernah dipegang."""
        with self._lock:
            result: dict[int, list[int]] = {}
            for item in self.items:
                if item.browser is not None:
                    result.setdefault(item.browser, []).append(item.index + 1)
            return result
