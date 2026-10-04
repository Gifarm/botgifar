import os
import logging

logger = logging.getLogger(__name__)

def get_next_prompt(prompt_file: str) -> str | None:
    """
    Mengambil 1 blok prompt (paragraf) pertama dari file.
    Prompt dipisahkan oleh baris kosong.
    Setelah diambil, blok tersebut dihapus dari file.
    """
    if not os.path.exists(prompt_file):
        logger.warning(f"File prompt tidak ditemukan: {prompt_file}")
        return None

    with open(prompt_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Memisahkan berdasarkan baris kosong (satu atau lebih)
    # Contoh: "Prompt A\n\nPrompt B" -> ["Prompt A", "Prompt B"]
    blocks = [block.strip() for block in content.split("\n\n") if block.strip()]

    if not blocks:
        logger.info("Tidak ada prompt tersisa.")
        return None

    # Ambil blok pertama
    next_prompt = blocks[0]
    
    # Simpan sisa blok kembali ke file
    remaining_blocks = blocks[1:]
    with open(prompt_file, "w", encoding="utf-8") as f:
        f.write("\n\n".join(remaining_blocks))

    logger.info(f"Prompt diambil: {next_prompt[:50]}...")
    return next_prompt