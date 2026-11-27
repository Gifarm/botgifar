"""
Bersihkan file lagu ganda di folder hasil setelah bot selesai.

Pemeriksaan ini murni membaca folder — tidak membuka atau menyentuh browser
sama sekali. Semua musik dari semua browser disatukan di `downloads/`; file
yang masih tertinggal di folder kerja browser (`state/download_tmp`) atau di
subfolder lama `downloads/.browser_N` dipindahkan dulu ke `downloads/`, lalu
semua file audio di folder itu dibaca menjadi satu daftar dan dibandingkan.

Kadang satu lagu terunduh dua kali (mis. perintah "Download" terkirim ganda,
atau browser mengulang unduhan yang sama). Chrome lalu menyimpan salinannya
sebagai "Judul (1).mp3", dan bot sendiri memberi akhiran "_1" kalau nama
tujuan sudah dipakai. Hasilnya folder berisi lagu yang sama lebih dari sekali.

Alur: kumpulkan daftar file -> kelompokkan yang namanya sama -> susun daftar
duplikat -> hapus duplikatnya. Sisa file adalah lagu yang tidak duplikat.
Aturan penghapusan sengaja konservatif, supaya lagu asli tidak ikut terhapus:

- hanya file audio yang namanya sama setelah akhiran " (1)", " (2)", "_1", ...
  dibuang;
- salinan hanya dihapus kalau **ukuran dan isi (SHA-1) sama persis** dengan
  file yang dipertahankan;
- file dengan nama asli (tanpa akhiran "(1)"/"_1") dipertahankan lebih dulu;
- kalau namanya mirip tetapi isinya berbeda (dua lagu asli yang kebetulan
  berjudul sama), keduanya dibiarkan dan dicatat di log.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
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


def _on_walk_error(error: OSError) -> None:
    logger.warning(f"Tidak bisa membaca folder download: {error}")


def _list_audio_files(root: Path) -> list[Path]:
    """
    Kumpulkan SEMUA file audio di `root` dan seluruh subfoldernya.

    Subfolder tersembunyi seperti `.browser_1` ikut diperiksa. Hanya file
    tersembunyi (mis. `.crdownload` yang sedang ditulis) yang dilewati.
    """
    files: list[Path] = []
    if not root.exists():
        return files

    for dirpath, _dirnames, filenames in os.walk(root, onerror=_on_walk_error):
        for name in filenames:
            if name.startswith("."):
                continue  # file sementara browser / file tersembunyi
            path = Path(dirpath) / name
            if path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            try:
                if path.is_file():
                    files.append(path)
            except OSError:
                continue
    return files


def _pick_keeper(files: list[Path], root: Path) -> Path:
    """
    Pilih file yang dipertahankan.

    Urutan prioritas: file di folder `downloads/` utama lebih dulu, lalu file
    yang namanya tanpa akhiran "(1)"/"_1", lalu file yang paling lama dibuat.
    """
    def sort_key(path: Path):
        number = _suffix_number(path.stem)
        in_root = path.parent == root
        try:
            mtime = path.stat().st_mtime
        except Exception:
            mtime = 0.0
        return (not in_root, number is not None, number or 0, mtime,
                path.name.casefold())

    return sorted(files, key=sort_key)[0]


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _unique_destination(directory: Path, filename: str) -> Path:
    """Nama tujuan yang belum dipakai, mis. "Lagu_1.mp3"."""
    base, ext = os.path.splitext(filename)
    if not ext:
        ext = ".mp3"
    destination = directory / f"{base}{ext}"
    counter = 1
    while destination.exists():
        destination = directory / f"{base}_{counter}{ext}"
        counter += 1
    return destination


def _remove_empty_dirs(root: Path) -> None:
    """Hapus folder kosong di bawah `root` (folder sementara yang sudah bersih)."""
    if not root.exists():
        return
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        path = Path(dirpath)
        if path == root:
            continue
        try:
            path.rmdir()  # hanya berhasil kalau benar-benar kosong
        except OSError:
            pass


def _remove_partial_downloads(root: Path) -> None:
    """Hapus sisa unduhan yang tidak selesai (`.crdownload`) di folder kerja."""
    if not root.exists():
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".crdownload"):
                continue
            path = Path(dirpath) / name
            try:
                path.unlink()
                logger.info(f"🧹 Unduhan tidak selesai dihapus: {path.name}")
            except OSError:
                pass


def consolidate_downloads(source_dirs, target_dir) -> int:
    """
    Pindahkan SEMUA file audio dari `source_dirs` ke satu folder hasil.

    Dipakai untuk menyatukan hasil semua browser: file yang masih tertinggal
    di folder kerja (atau folder lama `.browser_N`) dipindahkan ke
    `downloads/`, lalu folder sumber yang kosong dihapus.
    """
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    moved = 0
    for source in (Path(d) for d in source_dirs):
        if not source.exists():
            continue
        for path in _list_audio_files(source):
            try:
                if path.parent.resolve() == target.resolve():
                    continue
            except Exception:
                if path.parent == target:
                    continue
            destination = _unique_destination(target, path.name)
            try:
                shutil.move(str(path), str(destination))
            except Exception:
                try:
                    shutil.copy2(str(path), str(destination))
                    path.unlink()
                except Exception as e:
                    logger.warning(
                        f"Gagal memindahkan {path} ke {target}: {e}"
                    )
                    continue
            moved += 1
            logger.info(f"📦 Dipindahkan ke folder hasil: {destination.name}")

    for source in (Path(d) for d in source_dirs):
        _remove_partial_downloads(source)
        _remove_empty_dirs(source)
        try:
            source.rmdir()  # hapus folder kerja yang sudah kosong
        except OSError:
            pass
    return moved


def remove_duplicate_downloads(directory) -> dict[str, object]:
    """
    Periksa folder hasil lalu hapus salinan lagu ganda.

    Semua file audio di `directory` dan subfoldernya dibaca dulu menjadi satu
    daftar. Kalau masih ada file di subfolder (mis. folder lama
    `downloads/.browser_N`), file itu dipindahkan dulu ke `directory` supaya
    SEMUA musik dari semua browser ada di satu folder. Duplikat yang isinya
    sama persis disusun menjadi daftar hapus, lalu dihapus sendiri oleh fungsi
    ini (tanpa membuka browser).

    Mengembalikan ringkasan:
        deleted         jumlah file salinan yang dihapus
        freed           total byte yang dibebaskan
        groups          jumlah kelompok nama yang diperiksa
        kept_different  salinan yang namanya mirip tapi isinya beda (dibiarkan)
        scanned         jumlah file audio yang dipindai
        moved           file dari subfolder yang disatukan ke folder hasil
        deleted_files   daftar path (relatif) file yang dihapus
    """
    directory = Path(directory)
    result: dict[str, object] = {
        "deleted": 0,
        "freed": 0,
        "groups": 0,
        "kept_different": 0,
        "scanned": 0,
        "moved": 0,
        "deleted_files": [],
    }
    if not directory.exists():
        return result

    # 1. Satukan dulu file dari subfolder mana pun ke folder hasil. Ini juga
    #    membersihkan sisa folder lama `downloads/.browser_N`.
    subdirs = []
    try:
        subdirs = [
            p for p in directory.iterdir()
            if p.is_dir() and not p.is_symlink()
        ]
    except Exception as e:
        logger.warning(f"Tidak bisa membaca folder {directory}: {e}")
    if subdirs:
        result["moved"] = consolidate_downloads(subdirs, directory)

    # 2. Bersihkan sisa unduhan yang tidak selesai, lalu kumpulkan semua file
    #    audio di folder hasil menjadi satu daftar.
    _remove_partial_downloads(directory)
    audio_files = _list_audio_files(directory)
    result["scanned"] = len(audio_files)
    if not audio_files:
        return result

    groups: dict[tuple[str, str], list[Path]] = {}
    for path in audio_files:
        key = (_normalize_stem(path.stem), path.suffix.casefold())
        groups.setdefault(key, []).append(path)

    # 3. Susun daftar duplikat (belum dihapus).
    to_delete: list[tuple[Path, Path, int]] = []  # (salinan, asli, ukuran)
    for (base, ext), files in sorted(groups.items()):
        if len(files) < 2:
            continue
        result["groups"] = int(result["groups"]) + 1

        keeper = _pick_keeper(files, directory)
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
                result["kept_different"] = int(result["kept_different"]) + 1
                logger.info(
                    f"🧹 '{_relative(candidate, directory)}' namanya mirip "
                    f"'{_relative(keeper, directory)}' tapi ukurannya beda "
                    f"({size:,} vs {keeper_size:,} byte) — dibiarkan."
                )
                continue

            if not digest_checked:
                keeper_digest = _digest(keeper)
                digest_checked = True
            candidate_digest = _digest(candidate)
            if keeper_digest is None or candidate_digest is None:
                continue

            if candidate_digest != keeper_digest:
                result["kept_different"] = int(result["kept_different"]) + 1
                logger.info(
                    f"🧹 '{_relative(candidate, directory)}' ukurannya sama "
                    f"dengan '{_relative(keeper, directory)}' tapi isinya "
                    "beda — dibiarkan."
                )
                continue

            to_delete.append((candidate, keeper, size))

    if to_delete:
        logger.info(
            f"🧹 Daftar duplikat: {len(to_delete)} file akan dihapus "
            f"(dari {result['scanned']} file audio yang dipindai)."
        )
        for candidate, keeper, _size in to_delete:
            logger.info(
                f"🧹   - {_relative(candidate, directory)} "
                f"(salinan dari '{_relative(keeper, directory)}')"
            )
    else:
        logger.info("🧹 Tidak ada file duplikat di folder download.")

    # 3. Hapus sendiri file-file yang ada di daftar duplikat.
    deleted_files: list[str] = result["deleted_files"]  # type: ignore[assignment]
    for candidate, keeper, size in to_delete:
        try:
            candidate.unlink()
        except Exception as e:
            logger.warning(
                f"Gagal menghapus salinan {_relative(candidate, directory)}: {e}"
            )
            continue

        result["deleted"] = int(result["deleted"]) + 1
        result["freed"] = int(result["freed"]) + size
        deleted_files.append(_relative(candidate, directory))
        logger.info(
            f"🧹 Simpanan ganda dihapus: "
            f"'{_relative(candidate, directory)}' "
            f"(salinan dari '{_relative(keeper, directory)}')."
        )

    return result
