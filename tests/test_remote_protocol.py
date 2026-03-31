import unittest

from remote_protocol import (
    MessageValidationError,
    build_auth_message,
    build_error_message,
    build_noop_message,
    build_text_message,
    build_translation_message,
    parse_client_message,
    parse_server_message,
)


class RemoteProtocolTests(unittest.TestCase):
    def test_parse_client_message_rejects_unknown_type(self):
        with self.assertRaises(MessageValidationError):
            parse_client_message('{"type":"bogus"}')

    def test_parse_client_message_rejects_missing_fields(self):
        with self.assertRaises(MessageValidationError):
            parse_client_message('{"type":"text","request_id":"abc"}')

    def test_build_and_parse_auth_message_round_trip(self):
        message = parse_client_message(build_auth_message("token", "pi-host"))
        self.assertEqual(message["type"], "auth")
        self.assertEqual(message["client_id"], "pi-host")

    def test_build_and_parse_translation_message_round_trip(self):
        message = parse_server_message(build_translation_message("req-1", "猫", "cat"))
        self.assertEqual(message["translated_text"], "cat")

    def test_parse_server_message_rejects_bad_error_code(self):
        with self.assertRaises(MessageValidationError):
            parse_server_message('{"type":"error","request_id":"a","code":"NOPE","message":"bad"}')

    def test_noop_and_error_messages_remain_parseable(self):
        noop = parse_server_message(build_noop_message("req-1", "waiting_for_stability"))
        error = parse_server_message(build_error_message("req-2", "BAD_REQUEST", "bad"))
        self.assertEqual(noop["reason"], "waiting_for_stability")
        self.assertEqual(error["code"], "BAD_REQUEST")

    def test_text_message_round_trip(self):
        message = parse_client_message(build_text_message("req-1", "猫"))
        self.assertEqual(message["text"], "猫")


if __name__ == "__main__":
    unittest.main()
