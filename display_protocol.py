#!/usr/bin/env python3

import json


PROTOCOL_VERSION = 1
CLOSE_BAD_REQUEST = 4400
CLOSE_BAD_AUTH = 4401
CLOSE_INTERNAL_ERROR = 1011

CLIENT_MESSAGE_TYPES = {"auth", "display_text", "clear"}
SERVER_MESSAGE_TYPES = {"auth_ok", "ack", "error"}
ERROR_CODES = {
    "BAD_AUTH",
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

    _require_string(message, "request_id")
    if message_type == "clear":
        return message

    text = message.get("text")
    if not isinstance(text, str):
        raise MessageValidationError("`text` must be a string.")
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

    if message_type == "ack":
        _require_string(message, "request_id")
        return message

    _require_string(message, "request_id")
    code = _require_string(message, "code")
    _require_string(message, "message")
    if code not in ERROR_CODES:
        raise MessageValidationError(f"Unsupported error code: {code}")
    return message


def dumps(message):
    return json.dumps(message, separators=(",", ":"), sort_keys=True)


def build_auth_message(token, client_id, device="clear-oled-mac-camera"):
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


def build_display_text_message(request_id, text):
    return dumps({"type": "display_text", "request_id": request_id, "text": text})


def build_clear_message(request_id):
    return dumps({"type": "clear", "request_id": request_id})


def build_ack_message(request_id):
    return dumps({"type": "ack", "request_id": request_id})


def build_error_message(request_id, code, message):
    if code not in ERROR_CODES:
        raise MessageValidationError(f"Unsupported error code: {code}")
    return dumps({"type": "error", "request_id": request_id, "code": code, "message": message})
