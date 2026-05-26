import cv2
import numpy as np
import logging
from PIL import Image

logger = logging.getLogger(__name__)

def preprocess_image_for_ocr(img: Image.Image) -> np.ndarray:
    """
    Apply advanced preprocessing to an image before passing to PaddleOCR or Tesseract.
    Steps:
    1. Grayscale
    2. Denoising
    3. Adaptive thresholding
    4. Deskewing
    """
    # Convert PIL Image to OpenCV format
    cv_img = np.array(img)
    if len(cv_img.shape) == 3 and cv_img.shape[2] == 3:
        gray = cv2.cvtColor(cv_img, cv2.COLOR_RGB2GRAY)
    elif len(cv_img.shape) == 3 and cv_img.shape[2] == 4:
        gray = cv2.cvtColor(cv_img, cv2.COLOR_RGBA2GRAY)
    else:
        gray = cv_img

    # 1. Denoising (Median blur to remove salt and pepper noise while preserving edges)
    denoised = cv2.medianBlur(gray, 3)

    # 2. Adaptive Thresholding (Handles uneven lighting in scanned documents)
    thresh = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
    )

    # 3. Deskewing
    coords = np.column_stack(np.where(thresh == 0))
    if len(coords) > 0:
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle

        # If the angle is very small, we might just ignore it to avoid blurring
        if abs(angle) > 0.5:
            (h, w) = thresh.shape[:2]
            center = (w // 2, h // 2)
            M = cv2.getRotationMatrix2D(center, angle, 1.0)
            
            # Use white background for the rotation border
            thresh = cv2.warpAffine(thresh, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
            logger.debug(f"Deskewed image by {angle:.2f} degrees")

    return thresh
