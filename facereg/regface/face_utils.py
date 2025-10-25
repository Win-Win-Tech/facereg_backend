import numpy as np
from PIL import Image
import io
import logging

logger = logging.getLogger(__name__)

def get_face_encoding(image_bytes):
    try:
        import face_recognition  # Import inside function to avoid startup issues
        image = face_recognition.load_image_file(io.BytesIO(image_bytes))
        encodings = face_recognition.face_encodings(image)
        if not encodings:
            logger.info("No face found in the uploaded image.")
            return None
        return encodings[0]
    except Exception as e:
        logger.exception("Error during face encoding: %s", e)
        return None

def match_face(uploaded_encoding, known_encodings, tolerance=0.6):
    try:
        import face_recognition  # Import inside function to avoid startup issues
        matches = face_recognition.compare_faces(known_encodings, uploaded_encoding, tolerance=tolerance)
        return matches
    except Exception as e:
        logger.exception("Error during face comparison: %s", e)
        return []