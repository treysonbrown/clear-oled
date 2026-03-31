#!/usr/bin/env python3

import json


PROTOCOL_VERSION = 1
CLOSE_BAD_REQUEST = 4400
CLOSE_BAD_AUTH = 4401
CLOSE_INTERNAL_ERROR = 1011

CLIENT_MESSAGE_TYPES = {"auth", "frame", "text"}
SERVER_MESSAGE_TYPES = {"auth_ok", "translation", "noop", "error"}
NOOP_REASONS = {"waiting_for_stability", "no_japanese", "duplicate_translation"}
ERROR_CODES = {
    "BAD_AUTH",
    "BAD_IMAGE",
    "OCR_FAILURE",
    "TRANSLATION_FAILURE",
    "BAD_REQUEST",
}


class MessageValidationError(ValueError):
    pass


def _require_string(message, field):
    value = message.get(field)
    if not isinstance(value, str) or not value:
        raise MessageValidationError(f"`{field}` must be a non-empty string.")
    return value


def _require_int(message, field):
    value = message.get(field)
    if not isinstance(value, int):
        raise MessageValidationError(f"`{field}` must be an integer.")
    return value


def _parse_json(raw_message):
    try:
        message = json.loads(raw_message)
    except json.JSONDecodeError as exc:
        raise MessageValidationError("Message is not valid JSON.") from exc

    if not isinstance(message, dict):
        raise MessageValidationError("Message payload must be a JSON object.")

    message_type = message.get("type")
    if not isinstance(message_type, str):
        raise MessageValidationError("`type` must be a string.")

    return message


def parse_client_message(raw_message):
    message = _parse_json(raw_message)
    message_type = message["type"]
    if message_type not in CLIENT_MESSAGE_TYPES:
        raise MessageValidationError(f"Unsupported client message type: {message_type}")

    if message_type == "auth":
        _require_int(message, "protocol_version")
        _require_string(message, "token")
        _require_string(message, "client_id")
        _require_string(message, "device")
        return message

    if message_type == "frame":
        _require_string(message, "request_id")
        _require_string(message, "captured_at")
        _require_string(message, "image_jpeg_base64")
        _require_int(message, "source_width")
        _require_int(message, "source_height")
        _require_int(message, "crop_width")
        _require_int(message, "crop_height")
        _require_string(message, "ocr_lang")
        _require_string(message, "ocr_psm")
        return message

    _require_string(message, "request_id")
    _require_string(message, "text")
    return message


def parse_server_message(raw_message):
    message = _parse_json(raw_message)
    message_type = message["type"]
    if message_type not in SERVER_MESSAGE_TYPES:
        raise MessageValidationError(f"Unsupported server message type: {message_type}")

    if message_type == "auth_ok":
        _require_int(message, "protocol_version")
        _require_string(message, "session_id")
        return message

    if message_type == "translation":
        _require_string(message, "request_id")
        _require_string(message, "source_text")
        _require_string(message, "translated_text")
        return message

    if message_type == "noop":
        _require_string(message, "request_id")
        reason = _require_string(message, "reason")
        if reason not in NOOP_REASONS:
            raise MessageValidationError(f"Unsupported noop reason: {reason}")
        return message

    _require_string(message, "request_id")
    code = _require_string(message, "code")
    _require_string(message, "message")
    if code not in ERROR_CODES:
        raise MessageValidationError(f"Unsupported error code: {code}")
    return message


def dumps(message):
    return json.dumps(message, separators=(",", ":"), sort_keys=True)


def build_auth_message(token, client_id, device="clear-oled"):
    return dumps(
        {
            "type": "auth",
            "protocol_version": PROTOCOL_VERSION,
            "token": token,
            "client_id": client_id,
            "device": device,
        }
    )


def build_auth_ok_message(session_id):
    return dumps(
        {
            "type": "auth_ok",
            "protocol_version": PROTOCOL_VERSION,
            "session_id": session_id,
        }
    )


def build_frame_message(
    request_id,
    captured_at,
    image_jpeg_base64,
    source_width,
    source_height,
    crop_width,
    crop_height,
    ocr_lang,
    ocr_psm,
):
    return dumps(
        {
            "type": "frame",
            "request_id": request_id,
            "captured_at": captured_at,
            "image_jpeg_base64": image_jpeg_base64,
            "source_width": source_width,
            "source_height": source_height,
            "crop_width": crop_width,
            "crop_height": crop_height,
            "ocr_lang": ocr_lang,
            "ocr_psm": ocr_psm,
        }
    )


def build_text_message(request_id, text):
    return dumps({"type": "text", "request_id": request_id, "text": text})


def build_translation_message(request_id, source_text, translated_text):
    return dumps(
        {
            "type": "translation",
            "request_id": request_id,
            "source_text": source_text,
            "translated_text": translated_text,
        }
    )


def build_noop_message(request_id, reason):
    if reason not in NOOP_REASONS:
        raise MessageValidationError(f"Unsupported noop reason: {reason}")
    return dumps({"type": "noop", "request_id": request_id, "reason": reason})


def build_error_message(request_id, code, message):
    if code not in ERROR_CODES:
        raise MessageValidationError(f"Unsupported error code: {code}")
    return dumps({"type": "error", "request_id": request_id, "code": code, "message": message})
