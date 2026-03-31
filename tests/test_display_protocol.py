import unittest

from display_protocol import (
    MessageValidationError,
    build_ack_message,
    build_auth_message,
    build_display_text_message,
    build_error_message,
    parse_client_message,
    parse_server_message,
)


class DisplayProtocolTests(unittest.TestCase):
    def test_parse_client_message_rejects_unknown_type(self):
        with self.assertRaises(MessageValidationError):
            parse_client_message('{"type":"bogus"}')

    def test_parse_client_message_rejects_missing_fields(self):
        with self.assertRaises(MessageValidationError):
            parse_client_message('{"type":"display_text","request_id":"abc"}')

    def test_build_and_parse_auth_message_round_trip(self):
        message = parse_client_message(build_auth_message("token", "macbook"))
        self.assertEqual(message["type"], "auth")
        self.assertEqual(message["client_id"], "macbook")

    def test_build_and_parse_display_text_message_round_trip(self):
        message = parse_client_message(build_display_text_message("req-1", "cat"))
        self.assertEqual(message["text"], "cat")

    def test_build_and_parse_ack_message_round_trip(self):
        message = parse_server_message(build_ack_message("req-1"))
        self.assertEqual(message["request_id"], "req-1")

    def test_parse_server_message_rejects_bad_error_code(self):
        with self.assertRaises(MessageValidationError):
            parse_server_message('{"type":"error","request_id":"a","code":"NOPE","message":"bad"}')

    def test_error_message_round_trip(self):
        message = parse_server_message(build_error_message("req-2", "BAD_REQUEST", "bad"))
        self.assertEqual(message["code"], "BAD_REQUEST")

    def test_display_text_allows_empty_string(self):
        message = parse_client_message(build_display_text_message("req-3", ""))
        self.assertEqual(message["text"], "")


if __name__ == "__main__":
    unittest.main()
