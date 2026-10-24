"""OCR of evidence screenshots with the configured model; results cached on disk."""
import base64
import hashlib
from pathlib import Path

from .llm import chat_json

PROMPT = (
    "You are transcribing a screenshot of an internal business console. Transcribe ALL visible content "
    "faithfully and literally (never guess or normalise values; keep emails exactly as shown, including case). "
    "Return a JSON object: {\"console\": <title of the tool>, \"captured_at\": <timestamp visible on the screen or null>, "
    "\"query\": <search box text or null>, \"table\": {\"columns\": [...], \"rows\": [[...], ...]}, "
    "\"detail\": {<label>: <value> for every label/value pair in the side detail panel}, "
    "\"activity\": [<each activity/timeline entry as a string>], \"other\": {<any other visible labelled values>}}. "
    "Use null for empty cells shown as a dash."
)


def ocr_image(path: Path):
    data = Path(path).read_bytes()
    b64 = base64.b64encode(data).decode()
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64, "detail": "high"}},
            ],
        }
    ]
    return chat_json("ocr", msgs, hashlib.sha256(data).hexdigest() + PROMPT)
