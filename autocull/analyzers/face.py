import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from typing import Dict, Any, Optional
import os

# Eye Landmarks
# Right eye (from user's perspective, so left side of image)
RIGHT_EYE_TOP = 159
RIGHT_EYE_BOTTOM = 145
RIGHT_EYE_INNER = 133
RIGHT_EYE_OUTER = 33

# Left eye (right side of image)
LEFT_EYE_TOP = 386
LEFT_EYE_BOTTOM = 374
LEFT_EYE_INNER = 362
LEFT_EYE_OUTER = 263

# Mouth Landmarks
MOUTH_TOP = 13
MOUTH_BOTTOM = 14
MOUTH_LEFT = 78
MOUTH_RIGHT = 308

# Thresholds
EAR_THRESHOLD = 0.15  # Below this, eyes are likely closed or blinking
MAR_THRESHOLD = 0.60  # Above this, mouth is extremely distorted/open wide (grimace)

_face_landmarker = None

def _get_face_landmarker():
    global _face_landmarker
    if _face_landmarker is None:
        model_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "face_landmarker.task")
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"MediaPipe face landmarker model not found at {model_path}")
            
        base_options = python.BaseOptions(model_asset_path=model_path)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
            num_faces=1
        )
        _face_landmarker = vision.FaceLandmarker.create_from_options(options)
    return _face_landmarker


def _euclidean_dist(p1, p2) -> float:
    return np.sqrt((p1.x - p2.x)**2 + (p1.y - p2.y)**2)


def evaluate_facial_expression(image_bgr: np.ndarray, bbox: tuple) -> Dict[str, Any]:
    """
    Evaluates the facial expression (eyes and mouth) of the subject.
    
    Args:
        image_bgr: The full BGR image.
        bbox: (x1, y1, x2, y2) bounding box of the subject (from YOLO).
        
    Returns:
        Dict containing:
        - face_detected: bool
        - eyes_closed: bool
        - bad_grimace: bool
        - expression_penalty: float (0.0 means good, 1.0 means terrible)
    """
    x1, y1, x2, y2 = bbox
    
    h = y2 - y1
    y2_head = y1 + int(h * 0.4)
    
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2_head = min(image_bgr.shape[1], x2), min(image_bgr.shape[0], y2_head)
    
    if x2 <= x1 or y2_head <= y1:
        return {"face_detected": False, "eyes_closed": False, "bad_grimace": False, "expression_penalty": 0.0}
        
    head_crop = image_bgr[y1:y2_head, x1:x2]
    rgb_crop = cv2.cvtColor(head_crop, cv2.COLOR_BGR2RGB)
    
    # Run Mediapipe Task API
    detector = _get_face_landmarker()
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_crop)
    results = detector.detect(mp_image)
    
    if not results.face_landmarks:
        return {"face_detected": False, "eyes_closed": False, "bad_grimace": False, "expression_penalty": 0.0}
        
    landmarks = results.face_landmarks[0]
    
    # Calculate EAR (Eye Aspect Ratio)
    # Right Eye
    r_top = landmarks[RIGHT_EYE_TOP]
    r_bottom = landmarks[RIGHT_EYE_BOTTOM]
    r_inner = landmarks[RIGHT_EYE_INNER]
    r_outer = landmarks[RIGHT_EYE_OUTER]
    r_ear = _euclidean_dist(r_top, r_bottom) / (_euclidean_dist(r_inner, r_outer) + 1e-6)
    
    # Left Eye
    l_top = landmarks[LEFT_EYE_TOP]
    l_bottom = landmarks[LEFT_EYE_BOTTOM]
    l_inner = landmarks[LEFT_EYE_INNER]
    l_outer = landmarks[LEFT_EYE_OUTER]
    l_ear = _euclidean_dist(l_top, l_bottom) / (_euclidean_dist(l_inner, l_outer) + 1e-6)
    
    avg_ear = (r_ear + l_ear) / 2.0
    
    # Calculate MAR (Mouth Aspect Ratio)
    m_top = landmarks[MOUTH_TOP]
    m_bottom = landmarks[MOUTH_BOTTOM]
    m_left = landmarks[MOUTH_LEFT]
    m_right = landmarks[MOUTH_RIGHT]
    mar = _euclidean_dist(m_top, m_bottom) / (_euclidean_dist(m_left, m_right) + 1e-6)
    
    eyes_closed = avg_ear < EAR_THRESHOLD
    bad_grimace = mar > MAR_THRESHOLD
    
    penalty = 0.0
    if eyes_closed:
        penalty += 0.6
    if bad_grimace:
        penalty += 0.3
        
    return {
        "face_detected": True,
        "eyes_closed": bool(eyes_closed),
        "bad_grimace": bool(bad_grimace),
        "expression_penalty": min(1.0, penalty)
    }

if __name__ == '__main__':
    img = np.zeros((500, 500, 3), dtype=np.uint8)
    res = evaluate_facial_expression(img, (100, 100, 400, 400))
    print("Test result:", res)
