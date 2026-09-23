import io
import os
import logging
from typing import Tuple

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageSequence

logger = logging.getLogger("NSFWDetector")

MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "resnet_50_1by2_nsfw.onnx")

# Порог срабатывания нейросети (0.70 = 70% уверенности)
NSFW_THRESHOLD = 0.70


class NSFWDetector:
    def __init__(self, model_path: str = MODEL_PATH):
        self.model_path = model_path
        self.session = None
        self._load_model()

    def _load_model(self):
        if not os.path.exists(self.model_path):
            logger.warning(f"Файл модели не найден по пути: {self.model_path}")
            return
        try:
            # Ограничиваем количество потоков для минимального потребления CPU
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            self.session = ort.InferenceSession(
                self.model_path,
                sess_options=options,
                providers=["CPUExecutionProvider"]
            )
            self.input_name = self.session.get_inputs()[0].name
            logger.info("Нейросеть NSFW анализатора успешно инициализирована.")
        except Exception as e:
            logger.error(f"Ошибка загрузки ONNX модели: {e}")
            self.session = None

    def preprocess(self, img: Image.Image) -> np.ndarray:
        """Подготовка изображения под вход ResNet-50 OpenNSFW (224x224, BGR, вычитание среднего)."""
        img = img.convert("RGB").resize((224, 224), Image.Resampling.BILINEAR)
        arr = np.array(img, dtype=np.float32)
        # RGB -> BGR
        arr = arr[:, :, ::-1]
        # Вычитание средних значений Caffe OpenNSFW
        arr[:, :, 0] -= 102.9801  # B
        arr[:, :, 1] -= 115.9465  # G
        arr[:, :, 2] -= 122.7717  # R
        # HWC в CHW
        arr = arr.transpose((2, 0, 1))
        # Batch dimension: 1 x 3 x 224 x 224
        return np.expand_dims(arr, axis=0)

    def classify_image(self, img: Image.Image) -> float:
        """Возвращает вероятность NSFW от 0.0 до 1.0 для одного изображения/кадра."""
        if not self.session:
            return 0.0
        try:
            input_tensor = self.preprocess(img)
            out = self.session.run(None, {self.input_name: input_tensor})[0][0]
            # out: [prob_sfw, prob_nsfw]
            nsfw_prob = float(out[1])
            return nsfw_prob
        except Exception as e:
            logger.error(f"Ошибка инференса нейросети: {e}")
            return 0.0

    def analyze_bytes(self, data: bytes, is_gif: bool = False, sample_frames: int = 5) -> Tuple[bool, float, str]:
        """
        Анализирует байты картинки или GIF.
        Для GIF извлекает несколько кадров из разных частей анимации и проверяет каждый.
        """
        if not self.session:
            return False, 0.0, "Модель не загружена"

        try:
            with Image.open(io.BytesIO(data)) as img:
                # Проверка, является ли изображение анимацией (GIF / WebP)
                n_frames = getattr(img, "n_frames", 1)

                if is_gif or n_frames > 1:
                    max_score = 0.0
                    detected_frame = -1

                    # Выбираем ключевые кадры анимации
                    if n_frames <= sample_frames:
                        frame_indices = list(range(n_frames))
                    else:
                        step = (n_frames - 1) / (sample_frames - 1)
                        frame_indices = [int(round(i * step)) for i in range(sample_frames)]

                    for f_idx in frame_indices:
                        img.seek(f_idx)
                        frame_score = self.classify_image(img)
                        if frame_score > max_score:
                            max_score = frame_score
                            detected_frame = f_idx

                        if frame_score >= NSFW_THRESHOLD:
                            return True, frame_score, f"Нейросеть обнаружила 18+ контент в кадре #{detected_frame + 1} гифки (вероятность: {frame_score * 100:.1f}%)"

                    return False, max_score, ""
                else:
                    # Обычная статичная картинка (.png, .jpg, .webp)
                    score = self.classify_image(img)
                    if score >= NSFW_THRESHOLD:
                        return True, score, f"Нейросеть обнаружила 18+ контент на изображении (вероятность: {score * 100:.1f}%)"
                    return False, score, ""

        except Exception as e:
            logger.debug(f"Не удалось проанализировать изображение: {e}")
            return False, 0.0, f"Ошибка чтения: {e}"


# Синглтон детектора для переиспользования
detector = NSFWDetector()
