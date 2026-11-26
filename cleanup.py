"""
Bersihkan file lagu ganda di folder downloads setelah bot selesai.

Kadang satu lagu terunduh dua kali (mis. perintah "Download" terkirim ganda,
atau browser mengulang unduhan yang sama). Chrome lalu menyimpan salinannya
sebagai "Judul (1).mp3", dan bot sendiri memberi akhiran "_1" kalau nama
tujuan sudah dipakai. Hasilnya folder berisi lagu yang sama lebih dari sekali.

Aturan penghapusan sengaja konservatif, supaya lagu asli tidak ikut terhapus:

- hanya file audio (mp3/wav/m4a/flac/ogg/opus) yang namanya sama setelah
  akhiran " (1)", " (2)", "_1", ... dibuang;
- salinan hanya dihapus kalau **ukuran dan isi (SHA-1) sama persis** dengan
  file yang dipertahankan;
- kalau namanya mirip tetapi isinya berbeda (dua lagu asli yang kebetulan
  berjudul sama), keduanya dibiarkan dan dicatat di log.
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma",
}

# "Judul (1)" (cara Chrome) atau "Judul_1" (cara bot menghindari nama bentrok)
_SUFFIX_PATTERN = re.compile(r"(?:\s*\((\d+)\)|_(\d+))$")


def _normalize_stem(stem: str) -> str:
    """Buang akhiran salinan berulang: "Judul (1) (2)" -> "Judul"."""
    base = stem
    while True:
        stripped = _SUFFIX_PATTERN.sub("", base).strip()
        if not stripped or stripped == base:
            break
        base = stripped
    return base.casefold()


def _suffix_number(stem: str) -> int | None:
    match = _SUFFIX_PATTERN.search(stem)
    if match is None:
        return None
    return int(match.group(1) or match.group(2))


def _digest(path: Path, chunk_size: int = 1 << 20) -> str | None:
    hasher = hashlib.sha1()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
    except Exception as e:
        logger.warning(f"Tidak bisa membaca {path.name} untuk dibandingkan: {e}")
        return None
    return hasher.hexdigest()


def _list_audio_files(directory: Path) -> list[Path]:
    files: list[Path] = []
    try:
        entries = list(directory.iterdir())
    except Exception as e:
        logger.warning(f"Tidak bisa membaca folder {directory}: {e}")
        return files

    for path in entries:
        name = path.name
        if name.startswith("."):
            continue  # folder .browser_N, file .crdownload sementara, dsb.
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        try:
            if path.is_file():
                files.append(path)
        except Exception:
            continue
    return files


def _pick_keeper(files: list[Path]) -> Path:
    """Pilih file yang dipertahankan: yang namanya tanpa akhiran (1)/_1."""
    def sort_key(path: Path):
        number = _suffix_number(path.stem)
        try:
            mtime = path.stat().st_mtime
        except Exception:
            mtime = 0.0
        return (number is not None, number or 0, mtime, path.name.casefold())

    return sorted(files, key=sort_key)[0]


def remove_duplicate_downloads(directory) -> dict[str, int]:
    """
    Hapus salinan lagu ganda di `directory`.

    Mengembalikan ringkasan:
        deleted         jumlah file salinan yang dihapus
        freed           total byte yang dibebaskan
        groups          jumlah kelompok nama yang diperiksa
        kept_different  salinan yang namanya mirip tapi isinya beda (dibiarkan)
    """
    directory = Path(directory)
    result = {
        "deleted": 0,
        "freed": 0,
        "groups": 0,
        "kept_different": 0,
    }
    if not directory.exists():
        return result

    groups: dict[tuple[str, str], list[Path]] = {}
    for path in _list_audio_files(directory):
        key = (_normalize_stem(path.stem), path.suffix.casefold())
        groups.setdefault(key, []).append(path)

    for (base, ext), files in sorted(groups.items()):
        if len(files) < 2:
            continue
        result["groups"] += 1

        keeper = _pick_keeper(files)
        try:
            keeper_size = keeper.stat().st_size
        except Exception:
            continue
        if keeper_size <= 0:
            logger.warning(
                f"🧹 '{keeper.name}' berukuran 0 byte — dilewati (tidak dihapus)."
            )
            continue

        keeper_digest: str | None = None
        digest_checked = False

        for candidate in files:
            if candidate == keeper:
                continue
            try:
                size = candidate.stat().st_size
            except Exception:
                continue
            if size != keeper_size:
                # Nama mirip tapi ukuran beda: hampir pasti lagu berbeda.
                result["kept_different"] += 1
                logger.info(
                    f"🧹 '{candidate.name}' namanya mirip '{keeper.name}' tapi "
                    f"ukurannya beda ({size:,} vs {keeper_size:,} byte) — dibiarkan."
                )
                continue

            if not digest_checked:
                keeper_digest = _digest(keeper)
                digest_checked = True
            candidate_digest = _digest(candidate)
            if keeper_digest is None or candidate_digest is None:
                continue

            if candidate_digest != keeper_digest:
                result["kept_different"] += 1
                logger.info(
                    f"🧹 '{candidate.name}' ukurannya sama dengan "
                    f"'{keeper.name}' tapi isinya beda — dibiarkan."
                )
                continue

            try:
                candidate.unlink()
            except Exception as e:
                logger.warning(f"Gagal menghapus salinan {candidate.name}: {e}")
                continue

            result["deleted"] += 1
            result["freed"] += size
            logger.info(
                f"🧹 Simpanan ganda dihapus: '{candidate.name}' "
                f"(salinan dari '{keeper.name}')."
            )

    return result
