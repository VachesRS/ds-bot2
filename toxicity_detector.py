import os
import logging
import urllib.request
import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

logger = logging.getLogger("ToxicityDetector")

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
MODEL_PATH = os.path.join(MODEL_DIR, "rubert_tiny_toxicity.onnx")
TOKENIZER_PATH = os.path.join(MODEL_DIR, "toxicity_tokenizer.json")

HF_MODEL_URL = "https://huggingface.co/aafoninsky/rubert-tiny-toxicity-onnx/resolve/main/model.onnx"
HF_TOKENIZER_URL = "https://huggingface.co/aafoninsky/rubert-tiny-toxicity-onnx/raw/main/tokenizer.json"

LABELS = ["non-toxic", "insult", "obscenity", "threat", "dangerous"]


class ToxicityDetector:
    """
    Локальный детектор оскорблений и токсичности на базе RuBERT-Tiny (ONNX).
    Работает полностью автономно на CPU без использования внешних API.
    """

    def __init__(self):
        self._ensure_files()
        logger.info("Инициализация ONNX сессии RuBERT Toxicity детектора...")
        
        # Настройка оптимизации ONNX Runtime для CPU
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = 2
        
        self.session = ort.InferenceSession(
            MODEL_PATH,
            sess_options=opts,
            providers=["CPUExecutionProvider"]
        )
        self.tokenizer = Tokenizer.from_file(TOKENIZER_PATH)
        logger.info("RuBERT Toxicity детектор успешно загружен и готов к анализу.")

    def _ensure_files(self):
        """Проверяет наличие модели и токенизатора, скачивает при отсутствии."""
        os.makedirs(MODEL_DIR, exist_ok=True)

        if not os.path.exists(TOKENIZER_PATH):
            logger.info("Скачивание токенизатора RuBERT...")
            urllib.request.urlretrieve(HF_TOKENIZER_URL, TOKENIZER_PATH)

        if not os.path.exists(MODEL_PATH):
            logger.info("Скачивание ONNX модели RuBERT (45 МБ)...")
            urllib.request.urlretrieve(HF_MODEL_URL, MODEL_PATH)

    def analyze(self, text: str) -> dict[str, float]:
        """
        Анализирует текст и возвращает вероятности (от 0.0 до 1.0) для всех категорий:
        'non-toxic', 'insult', 'obscenity', 'threat', 'dangerous'
        """
        if not text or not text.strip():
            return {label: 0.0 for label in LABELS}

        # Токенизация с ограничением длины в 128 токенов
        enc = self.tokenizer.encode(text)
        ids = enc.ids[:128]
        mask = enc.attention_mask[:128]
        type_ids = enc.type_ids[:128]

        input_ids = np.array([ids], dtype=np.int64)
        attn_mask = np.array([mask], dtype=np.int64)
        token_types = np.array([type_ids], dtype=np.int64)

        # Подготовка входных данных для модели
        inputs = {}
        for inp in self.session.get_inputs():
            name = inp.name
            if "token_type" in name or "type_ids" in name:
                inputs[name] = token_types
            elif "attention" in name or "mask" in name:
                inputs[name] = attn_mask
            else:
                inputs[name] = input_ids

        # Выполнение инференса
        logits = self.session.run(None, inputs)[0][0]
        
        # Сигмоида для многоклассовой классификации
        probs = 1.0 / (1.0 + np.exp(-logits))

        return {LABELS[i]: float(probs[i]) for i in range(len(LABELS))}

    def is_insult(self, text: str, threshold: float = 0.85) -> tuple[bool, float]:
        """
        Проверяет, содержит ли текст оскорбление.
        Возвращает кортеж: (Является ли оскорблением, Уверенность нейросети).
        """
        if not text or len(text.strip()) < 3:
            return False, 0.0

        scores = self.analyze(text)
        insult_score = scores.get("insult", 0.0)

        return (insult_score >= threshold), insult_score
