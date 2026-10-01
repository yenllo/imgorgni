"""
Subject Detector — YOLOv8n ONNX person detection.

Detects people in sports photography using YOLOv8n (Nano, ~6MB).
Runs on ONNX Runtime with automatic backend fallback:
  1. NPU (AMD XDNA via VitisAI EP) — fastest
  2. DirectML (iGPU) — fast
  3. CPU — always works

For night football, confidence threshold is lowered to 0.3 (default YOLO is 0.5)
because poor lighting reduces detector confidence.
"""

import os
import sys
from typing import Optional

import cv2
import numpy as np

from autocull.config import AnalysisConfig, DEFAULT_CONFIG


def _active_backend_name(session) -> str:
    """Friendly name of the provider ONNX Runtime *actually* registered.

    onnxruntime silently drops unavailable providers (e.g. DirectML/VitisAI
    when not installed) and falls back to CPU, so the requested backend is not
    a reliable label. ``get_providers()`` returns what really got registered.
    """
    providers = session.get_providers() if session is not None else []
    active = providers[0] if providers else "CPUExecutionProvider"
    return {
        "VitisAIExecutionProvider": "npu",
        "DmlExecutionProvider": "directml",
        "CPUExecutionProvider": "cpu",
    }.get(active, active)


class SubjectDetector:
    """YOLOv8n-based person detector using ONNX Runtime.

    Attributes:
        session: ONNX Runtime inference session.
        input_name: Name of the model's input tensor.
        input_size: Expected input image size (640x640).
        confidence_threshold: Minimum detection confidence.
        person_class_id: COCO class ID for 'person' (0).
    """

    def __init__(self, config: AnalysisConfig = DEFAULT_CONFIG):
        """Initialize the detector.

        Args:
            config: Analysis configuration with model path and backend settings.

        Raises:
            FileNotFoundError: If the ONNX model is not found.
            ImportError: If onnxruntime is not installed.
        """
        self.config = config
        self.input_size = config.yolo_input_size
        self.confidence_threshold = config.yolo_confidence_threshold
        self.person_class_id = config.yolo_person_class_id
        self.session = None
        self.input_name = None
        self._backend_name = "not initialized"

    def initialize(self) -> str:
        """Initialize the ONNX Runtime session with automatic backend fallback.

        Returns:
            Name of the backend being used ("npu", "directml", or "cpu").

        Raises:
            FileNotFoundError: If model file not found.
            RuntimeError: If no backend could be initialized.
        """
        try:
            import onnxruntime as ort
        except ImportError:
            raise ImportError(
                "onnxruntime is required for person detection. "
                "Install with: pip install onnxruntime"
            )

        model_path = self.config.yolo_model_path
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"YOLO model not found: {model_path}\n"
                f"Download it with: python -c \"from ultralytics import YOLO; "
                f"model = YOLO('yolov8n.pt'); model.export(format='onnx')\"\n"
                f"Then move yolov8n.onnx to {os.path.dirname(model_path)}/"
            )

        backend = self.config.yolo_backend
        session_options = ort.SessionOptions()
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        # Try backends in priority order
        providers_to_try = self._get_provider_list(backend)

        for provider_name, providers in providers_to_try:
            try:
                self.session = ort.InferenceSession(
                    model_path,
                    sess_options=session_options,
                    providers=providers,
                )
                self.input_name = self.session.get_inputs()[0].name
                self._backend_name = _active_backend_name(self.session)
                return self._backend_name
            except Exception:
                continue

        raise RuntimeError(
            "Could not initialize ONNX Runtime with any backend. "
            "Ensure onnxruntime is installed correctly."
        )

    def _get_provider_list(self, backend: str) -> list[tuple[str, list]]:
        """Get ordered list of providers to try.

        Args:
            backend: Requested backend ("auto", "cpu", "directml", "npu").

        Returns:
            List of (name, providers) tuples to try in order.
        """
        cpu = ("cpu", ["CPUExecutionProvider"])
        directml = ("directml", ["DmlExecutionProvider", "CPUExecutionProvider"])
        npu = ("npu", [
            ("VitisAIExecutionProvider", {"config_file": ""}),
            "CPUExecutionProvider",
        ])

        if backend == "cpu":
            return [cpu]
        elif backend == "directml":
            return [directml, cpu]
        elif backend == "npu":
            return [npu, directml, cpu]
        else:  # auto
            return [npu, directml, cpu]

    @property
    def backend_name(self) -> str:
        """Name of the active backend."""
        return self._backend_name

    def detect_persons(
        self,
        image: np.ndarray,
    ) -> list[dict]:
        """Detect persons in an image.

        Args:
            image: BGR numpy array.

        Returns:
            List of detections, each a dict with:
            - bbox: (x1, y1, x2, y2) in original image coordinates
            - confidence: float [0-1]
            - class_id: int (0 = person)
        """
        if self.session is None:
            self.initialize()

        # Preprocess
        input_tensor, ratio, (pad_w, pad_h) = self._preprocess(image)

        # Inference
        outputs = self.session.run(None, {self.input_name: input_tensor})

        # Postprocess
        detections = self._postprocess(
            outputs[0], image.shape, ratio, pad_w, pad_h
        )

        return detections

    def _preprocess(self, image: np.ndarray) -> tuple[np.ndarray, float, tuple[int, int]]:
        """Preprocess image for YOLOv8n input.

        Letterbox resize to 640x640, normalize to [0,1], CHW format.

        Args:
            image: BGR numpy array.

        Returns:
            Tuple of (input_tensor, resize_ratio, (pad_w, pad_h)).
        """
        h, w = image.shape[:2]
        target = self.input_size

        # Calculate resize ratio (maintain aspect ratio)
        ratio = min(target / h, target / w)
        new_h, new_w = int(h * ratio), int(w * ratio)

        # Resize
        resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        # Pad to square (letterbox)
        pad_h = (target - new_h) // 2
        pad_w = (target - new_w) // 2
        padded = cv2.copyMakeBorder(
            resized, pad_h, target - new_h - pad_h,
            pad_w, target - new_w - pad_w,
            cv2.BORDER_CONSTANT, value=(114, 114, 114)
        )

        # Normalize to [0, 1], convert BGR→RGB, HWC→CHW
        blob = padded.astype(np.float32) / 255.0
        blob = blob[:, :, ::-1]  # BGR → RGB
        blob = blob.transpose(2, 0, 1)  # HWC → CHW
        blob = np.expand_dims(blob, axis=0)  # Add batch dimension
        blob = np.ascontiguousarray(blob)

        return blob, ratio, (pad_w, pad_h)

    def _postprocess(
        self,
        output: np.ndarray,
        original_shape: tuple,
        ratio: float,
        pad_w: int,
        pad_h: int,
    ) -> list[dict]:
        """Postprocess YOLOv8n output to extract person detections.

        YOLOv8n output shape: (1, 84, 8400)
        - 84 = 4 (bbox: cx, cy, w, h) + 80 (class scores)
        - 8400 = number of anchor predictions

        Args:
            output: Raw model output array.
            original_shape: Original image shape (H, W, C).
            ratio: Resize ratio used in preprocessing.
            pad_w: Horizontal padding applied.
            pad_h: Vertical padding applied.

        Returns:
            List of person detections above confidence threshold.
        """
        orig_h, orig_w = original_shape[:2]

        # Transpose from (1, 84, 8400) to (8400, 84)
        predictions = output[0].T  # (8400, 84)

        # Extract boxes and class scores
        boxes = predictions[:, :4]  # cx, cy, w, h
        class_scores = predictions[:, 4:]  # 80 classes

        # Filter: person class (0) and sports ball class (32)
        person_scores = class_scores[:, self.person_class_id]
        ball_scores = class_scores[:, self.config.yolo_ball_class_id]
        
        # The ball gets its own, lower threshold: it's small, fast and
        # motion-blurred at night, and losing it costs us the subject anchor.
        person_mask = person_scores > self.confidence_threshold
        ball_mask = ball_scores > self.config.yolo_ball_confidence_threshold
        
        combined_mask = person_mask | ball_mask

        if not np.any(combined_mask):
            return []
            
        filtered_boxes = boxes[combined_mask]
        
        # Extract the highest score and its corresponding class for each bounding box
        f_person = person_scores[combined_mask]
        f_ball = ball_scores[combined_mask]
        
        filtered_scores = np.maximum(f_person, f_ball)
        filtered_class_ids = np.where(f_person > f_ball, self.person_class_id, self.config.yolo_ball_class_id)

        # Convert from center format to corner format
        x1 = filtered_boxes[:, 0] - filtered_boxes[:, 2] / 2
        y1 = filtered_boxes[:, 1] - filtered_boxes[:, 3] / 2
        x2 = filtered_boxes[:, 0] + filtered_boxes[:, 2] / 2
        y2 = filtered_boxes[:, 1] + filtered_boxes[:, 3] / 2

        # Remove padding and rescale to original image size
        x1 = (x1 - pad_w) / ratio
        y1 = (y1 - pad_h) / ratio
        x2 = (x2 - pad_w) / ratio
        y2 = (y2 - pad_h) / ratio

        # Clip to image boundaries
        x1 = np.clip(x1, 0, orig_w).astype(int)
        y1 = np.clip(y1, 0, orig_h).astype(int)
        x2 = np.clip(x2, 0, orig_w).astype(int)
        y2 = np.clip(y2, 0, orig_h).astype(int)

        # NMS (Non-Maximum Suppression)
        indices = self._nms(x1, y1, x2, y2, filtered_scores, iou_threshold=0.5)

        detections = []
        for i in indices:
            detections.append({
                "bbox": (int(x1[i]), int(y1[i]), int(x2[i]), int(y2[i])),
                "confidence": float(filtered_scores[i]),
                "class_id": int(filtered_class_ids[i]),
            })

        return detections

    @staticmethod
    def _nms(
        x1: np.ndarray, y1: np.ndarray,
        x2: np.ndarray, y2: np.ndarray,
        scores: np.ndarray,
        iou_threshold: float = 0.5,
    ) -> list[int]:
        """Non-Maximum Suppression to remove overlapping detections.

        Args:
            x1, y1, x2, y2: Bounding box corner coordinates.
            scores: Detection confidence scores.
            iou_threshold: IoU threshold for suppression.

        Returns:
            List of indices to keep.
        """
        areas = (x2 - x1) * (y2 - y1)
        order = scores.argsort()[::-1]

        keep = []
        while len(order) > 0:
            i = order[0]
            keep.append(i)

            if len(order) == 1:
                break

            # Compute IoU with remaining boxes
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])

            inter_w = np.maximum(0, xx2 - xx1)
            inter_h = np.maximum(0, yy2 - yy1)
            intersection = inter_w * inter_h

            union = areas[i] + areas[order[1:]] - intersection
            iou = intersection / (union + 1e-6)

            # Keep boxes with IoU below threshold
            remaining = np.where(iou <= iou_threshold)[0]
            order = order[remaining + 1]

        return keep


def detect_persons_simple(
    image: np.ndarray,
    config: AnalysisConfig = DEFAULT_CONFIG,
    detector: Optional[SubjectDetector] = None,
) -> list[dict]:
    """Simple function interface for person detection.

    Creates/reuses a detector instance and runs detection.

    Args:
        image: BGR numpy array.
        config: Analysis configuration.
        detector: Optional pre-initialized detector instance.

    Returns:
        List of person detections.
    """
    if detector is None:
        detector = SubjectDetector(config)
        detector.initialize()

    return detector.detect_persons(image)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        img = cv2.imread(sys.argv[1])
        if img is not None:
            detector = SubjectDetector()
            try:
                backend = detector.initialize()
                print(f"Backend: {backend}")
                detections = detector.detect_persons(img)
                print(f"Found {len(detections)} persons:")
                for d in detections:
                    print(f"  bbox={d['bbox']}, conf={d['confidence']:.2f}")
            except FileNotFoundError as e:
                print(f"Model not found: {e}")
    else:
        print("Usage: python -m autocull.analyzers.subject <image_path>")
