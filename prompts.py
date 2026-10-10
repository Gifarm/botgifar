import os
import logging
import threading

logger = logging.getLogger(__name__)

# Lock global biar 5 thread tidak baca/tulis file bareng
_prompt_lock = threading.Lock()


def get_next_prompt(prompt_file: str) -> str | None:
    """
    Mengambil 1 blok prompt (paragraf) pertama dari file.
    Prompt dipisahkan oleh baris kosong.
    Setelah diambil, blok tersebut dihapus dari file.
    """
    with _prompt_lock:
        if not os.path.exists(prompt_file):
            logger.warning(f"File prompt tidak ditemukan: {prompt_file}")
            return None

        try:
            with open(prompt_file, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.error(f"Gagal baca file prompt: {e}")
            return None

        blocks = [block.strip() for block in content.split("\n\n") if block.strip()]

        if not blocks:
            logger.info("Tidak ada prompt tersisa.")
            return None

        next_prompt = blocks[0]
        remaining_blocks = blocks[1:]

        try:
            with open(prompt_file, "w", encoding="utf-8") as f:
                f.write("\n\n".join(remaining_blocks))
        except Exception as e:
            logger.error(f"Gagal tulis file prompt: {e}")
            return next_prompt

        logger.info(f"✂️  Prompt diambil. Sisa: {len(remaining_blocks)}")
        return next_prompt


def return_prompt(prompt_file: str, prompt: str) -> bool:
    """
    Mengembalikan prompt ke DEPAN file (kalau gagal di-generate).
    Thread-safe.
    """
    if not prompt or not prompt.strip():
        return False

    with _prompt_lock:
        try:
            existing = ""
            if os.path.exists(prompt_file):
                with open(prompt_file, "r", encoding="utf-8") as f:
                    existing = f.read().strip()

            new_content = prompt.strip()
            if existing:
                new_content += "\n\n" + existing

            with open(prompt_file, "w", encoding="utf-8") as f:
                f.write(new_content)

            logger.info(f"↩️  Prompt dikembalikan ke file.")
            return True
        except Exception as e:
            logger.error(f"Gagal return prompt: {e}")
            return False


def count_prompts(prompt_file: str) -> int:
    """Hitung sisa prompt di file."""
    if not os.path.exists(prompt_file):
        return 0
    try:
        with open(prompt_file, "r", encoding="utf-8") as f:
            content = f.read()
        return len([b for b in content.split("\n\n") if b.strip()])
    except:
        return 0