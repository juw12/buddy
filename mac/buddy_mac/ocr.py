"""On-device text recognition with Apple's Vision framework: free, private, ~0.2-0.6 s per screenshot.

Buddy uses it to snap the arrow onto the exact text the brain named ("Save As") instead of trusting the
brain's rough coordinates. Nothing leaves the Mac.
"""
from __future__ import annotations

import logging


def read_text(jpeg: bytes) -> list[tuple[str, float, float, float, float]]:
    """[(text, x0, y0, x1, y1), ...] in 0..1000 coordinates of the image, top-left origin. [] on any failure."""
    try:
        import Vision
        from Foundation import NSData
    except ImportError:
        return []
    try:
        data = NSData.dataWithBytes_length_(jpeg, len(jpeg))
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(False)        # UI labels aren't sentences
        ok, error = handler.performRequests_error_([request], None)
        if not ok:
            logging.warning("text recognition failed: %s", error)
            return []
        boxes = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            box = observation.boundingBox()              # normalized, origin at the bottom-left
            x0 = box.origin.x * 1000
            x1 = (box.origin.x + box.size.width) * 1000
            y0 = (1 - box.origin.y - box.size.height) * 1000
            y1 = (1 - box.origin.y) * 1000
            boxes.append((str(candidates[0].string()), x0, y0, x1, y1))
        return boxes
    except Exception:                                    # noqa: BLE001 - never let OCR break an answer
        logging.exception("text recognition crashed")
        return []
