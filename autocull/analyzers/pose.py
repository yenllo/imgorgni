"""
Pose Detector — YOLOv8n-pose ONNX person and keypoint detection.

Extracts 17 keypoints per person to evaluate posture (facing camera, running).
"""

import os
import cv2
import numpy as np
from typing import Optional

from autocull.config import AnalysisConfig, DEFAULT_CONFIG
from autocull.analyzers.subject import SubjectDetector, _active_backend_name

class PoseDetector(SubjectDetector):
    """YOLOv8n-pose detector for human keypoints."""

    def __init__(self, config: AnalysisConfig = DEFAULT_CONFIG):
        super().__init__(config)
        self.input_size = config.yolo_input_size
        self.confidence_threshold = config.yolo_confidence_threshold
        # Override model path to pose model
        self.model_path = config.yolo_pose_model_path

    def initialize(self) -> str:
        try:
            import onnxruntime as ort
        except ImportError:
            raise ImportError("onnxruntime is required")

        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"YOLO pose model not found: {self.model_path}")

        session_options = ort.SessionOptions()
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        providers_to_try = self._get_provider_list(self.config.yolo_backend)

        for provider_name, providers in providers_to_try:
            try:
                self.session = ort.InferenceSession(
                    self.model_path,
                    sess_options=session_options,
                    providers=providers,
                )
                self.input_name = self.session.get_inputs()[0].name
                self._backend_name = _active_backend_name(self.session)
                return self._backend_name
            except Exception:
                continue

        raise RuntimeError("Could not initialize ONNX Runtime for Pose model.")

    def _postprocess(
        self,
        output: np.ndarray,
        original_shape: tuple,
        ratio: float,
        pad_w: int,
        pad_h: int,
    ) -> list[dict]:
        """Postprocess YOLOv8n-pose output.
        
        Output shape: (1, 56, 8400)
        - 0:3 -> cx, cy, w, h
        - 4 -> person confidence
        - 5:55 -> 17 keypoints (x, y, conf)
        """
        orig_h, orig_w = original_shape[:2]

        predictions = output[0].T  # (8400, 56)

        boxes = predictions[:, :4]
        scores = predictions[:, 4]
        keypoints = predictions[:, 5:]  # (8400, 51)

        mask = scores > self.confidence_threshold
        filtered_boxes = boxes[mask]
        filtered_scores = scores[mask]
        filtered_keypoints = keypoints[mask]

        if len(filtered_boxes) == 0:
            return []

        # Convert bbox
        x1 = filtered_boxes[:, 0] - filtered_boxes[:, 2] / 2
        y1 = filtered_boxes[:, 1] - filtered_boxes[:, 3] / 2
        x2 = filtered_boxes[:, 0] + filtered_boxes[:, 2] / 2
        y2 = filtered_boxes[:, 1] + filtered_boxes[:, 3] / 2

        x1 = (x1 - pad_w) / ratio
        y1 = (y1 - pad_h) / ratio
        x2 = (x2 - pad_w) / ratio
        y2 = (y2 - pad_h) / ratio

        x1 = np.clip(x1, 0, orig_w).astype(int)
        y1 = np.clip(y1, 0, orig_h).astype(int)
        x2 = np.clip(x2, 0, orig_w).astype(int)
        y2 = np.clip(y2, 0, orig_h).astype(int)

        # NMS
        indices = self._nms(x1, y1, x2, y2, filtered_scores, iou_threshold=0.5)

        detections = []
        for i in indices:
            # Extract and rescale keypoints
            raw_kps = filtered_keypoints[i].reshape(17, 3)
            kps = []
            for j in range(17):
                kx, ky, kconf = raw_kps[j]
                kx = (kx - pad_w) / ratio
                ky = (ky - pad_h) / ratio
                kps.append((kx, ky, kconf))

            detections.append({
                "bbox": (int(x1[i]), int(y1[i]), int(x2[i]), int(y2[i])),
                "confidence": float(filtered_scores[i]),
                "keypoints": kps
            })

        return detections


def evaluate_pose(detection: dict) -> dict:
    """Evaluate a single pose detection for aesthetic heuristics.
    
    Returns:
        Dict with:
        - face_visible: float (0.0 to 1.0)
        - dynamic_stride: float (0.0 to 1.0)
    """
    if "keypoints" not in detection:
        return {"face_visible": 0.0, "dynamic_stride": 0.0}
        
    kps = detection["keypoints"]
    # 0: Nose, 1: L-Eye, 2: R-Eye, 13: L-Knee, 14: R-Knee, 15: L-Ankle, 16: R-Ankle
    
    # 1. Face Visibility
    nose_conf = kps[0][2]
    leye_conf = kps[1][2]
    reye_conf = kps[2][2]
    
    # We consider the face visible if the nose and at least one eye have > 0.5 conf
    max_eye_conf = max(leye_conf, reye_conf)
    face_visible = min(1.0, (nose_conf + max_eye_conf)) / 2.0
    
    # 2. Dynamic Stride (Running vs Standing)
    # Distance between ankles normalized by person bounding box height
    x1, y1, x2, y2 = detection["bbox"]
    box_h = max(1, y2 - y1)
    
    lankle = kps[15]
    rankle = kps[16]
    
    # If both ankles are somewhat visible
    if lankle[2] > 0.3 and rankle[2] > 0.3:
        dx = lankle[0] - rankle[0]
        dy = lankle[1] - rankle[1]
        stride_dist = np.sqrt(dx**2 + dy**2)
        # Normalized stride (typically 0.1 to 0.5 of body height)
        norm_stride = stride_dist / box_h
        # Map 0.1->0.0 (standing), 0.3->1.0 (running)
        dynamic_stride = max(0.0, min(1.0, (norm_stride - 0.1) / 0.2))
    else:
        # If ankles not visible, try knees
        lknee = kps[13]
        rknee = kps[14]
        if lknee[2] > 0.3 and rknee[2] > 0.3:
            dx = lknee[0] - rknee[0]
            dy = lknee[1] - rknee[1]
            stride_dist = np.sqrt(dx**2 + dy**2)
            norm_stride = stride_dist / box_h
            dynamic_stride = max(0.0, min(1.0, (norm_stride - 0.1) / 0.2))
        else:
            dynamic_stride = 0.5 # Neutral fallback if legs aren't visible
            
    return {
        "face_visible": float(face_visible),
        "dynamic_stride": float(dynamic_stride)
    }
