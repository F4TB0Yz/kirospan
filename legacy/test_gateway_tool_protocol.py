#!/usr/bin/env python3
"""Strict regression tests for OpenAI <-> Kiro tool protocol conversion."""

import json
import unittest

import kiro_gateway
from kiro_gateway import (
    ChatRequest,
    _build_conversation_state,
    _ensure_token,
    _refresh_kiro_token,
    _stream_runtime,
    _token_expires_soon,
)
from unittest.mock import AsyncMock, patch


class TokenRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_token_is_refreshed_automatically(self):
        expired = {
            "access_token": "expired-access",
            "refresh_token": "refresh-token",
            "expires_at": "2000-01-01T00:00:00Z",
        }
        fresh = {
            **expired,
            "access_token": "fresh-access",
            "expires_at": "2099-01-01T00:00:00Z",
        }

        with (
            patch.object(kiro_gateway, "get_kiro_token", side_effect=[expired, expired]),
            patch.object(kiro_gateway, "_refresh_kiro_token", new=AsyncMock(return_value=fresh)) as refresh,
            patch.object(kiro_gateway, "_save_kiro_token") as save,
        ):
            result = await _ensure_token(object())

        self.assertEqual(result["access_token"], "fresh-access")
        refresh.assert_awaited_once()
        save.assert_called_once_with(fresh)

    async def test_desktop_refresh_is_persisted(self):
        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "accessToken": "new-access",
                    "refreshToken": "new-refresh",
                    "expiresIn": 3600,
                }

        class Client:
            async def post(self, url, **kwargs):
                self.url = url
                self.kwargs = kwargs
                return Response()

        client = Client()
        old = {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": "2000-01-01T00:00:00Z",
        }
        with patch.object(kiro_gateway, "_save_kiro_token") as save:
            result = await _refresh_kiro_token(client, old)

        self.assertEqual(result["access_token"], "new-access")
        self.assertEqual(result["refresh_token"], "new-refresh")
        self.assertFalse(_token_expires_soon(result))
        self.assertIn("auth.desktop.kiro.dev/refreshToken", client.url)
        save.assert_called_once_with(result)

    async def test_persistent_upstream_error_is_a_valid_openai_chunk(self):
        private_detail = "private prompt fragment: customer-123"

        class Response:
            status_code = 403

            async def aread(self):
                return f'{{"message":"{private_detail}"}}'.encode()

        class StreamContext:
            async def __aenter__(self):
                return Response()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class Client:
            def stream(self, *args, **kwargs):
                return StreamContext()

        with self.assertLogs("kirospan", level="ERROR") as logs:
            lines = [line async for line in _stream_runtime(
                Client(), "rejected", {"profileArn": "profile"}, "gpt-5.6-sol", _auth_retry=True
            )]
        payloads = [
            json.loads(line.removeprefix("data: ").strip())
            for line in lines
            if line.startswith("data: {")
        ]
        self.assertTrue(payloads)
        self.assertTrue(all("choices" in payload for payload in payloads))
        self.assertEqual(payloads[-1]["choices"][0]["finish_reason"], "stop")
        self.assertNotIn(private_detail, "".join(lines + logs.output))


class RequestCompatibilityTests(unittest.TestCase):
    def test_opencode_string_tool_choice_is_accepted(self):
        request = ChatRequest(
            model="gpt-5.6-sol",
            messages=[{"role": "user", "content": "Hello"}],
            tool_choice="auto",
        )

        self.assertEqual(request.tool_choice, "auto")


TOOLS = [{

    "type": "function",
    "function": {
        "name": "terminal",
        "description": "Execute a shell command",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
}]


class ConversationStateTests(unittest.TestCase):
    def test_system_before_user_still_advertises_tools_on_current_message(self):
        state = _build_conversation_state(
            "claude-sonnet-5",
            [
                {"role": "system", "content": "Be precise."},
                {"role": "user", "content": "What directory are we in?"},
            ],
            tools=TOOLS,
        )["conversationState"]

        current = state["currentMessage"]["userInputMessage"]
        self.assertEqual(current["content"], "Be precise.\n\nWhat directory are we in?")
        specs = current["userInputMessageContext"]["tools"]
        self.assertEqual(specs[0]["toolSpecification"]["name"], "terminal")
        self.assertNotIn("history", state)

    def test_tool_result_is_current_message_and_history_alternates_entries(self):
        state = _build_conversation_state(
            "claude-sonnet-5",
            [
                {"role": "system", "content": "Be precise."},
                {"role": "user", "content": "Run pwd."},
                {
                    "role": "assistant",
                    "content": "I'll check.",
                    "tool_calls": [{
                        "id": "tooluse_abc",
                        "type": "function",
                        "function": {"name": "terminal", "arguments": '{"command":"pwd"}'},
                    }],
                },
                {
                    "role": "tool",
                    "name": "terminal",
                    "tool_call_id": "tooluse_abc",
                    "content": "/workspace/project\n",
                },
            ],
            tools=TOOLS,
        )["conversationState"]

        history = state["history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(set(history[0]), {"userInputMessage"})
        self.assertEqual(set(history[1]), {"assistantResponseMessage"})
        self.assertEqual(
            history[1]["assistantResponseMessage"]["toolUses"][0],
            {
                "name": "terminal",
                "toolUseId": "tooluse_abc",
                "input": {"command": "pwd"},
            },
        )

        current = state["currentMessage"]["userInputMessage"]
        self.assertEqual(current["content"], "Tool results provided.")
        context = current["userInputMessageContext"]
        self.assertEqual(context["tools"][0]["toolSpecification"]["name"], "terminal")
        self.assertEqual(context["toolResults"], [{
            "content": [{"text": "/workspace/project\n"}],
            "status": "success",
            "toolUseId": "tooluse_abc",
        }])

    def test_completed_history_and_new_user_message(self):
        state = _build_conversation_state(
            "gpt-5.6-sol",
            [
                {"role": "system", "content": "System."},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "Run pwd"},
            ],
            tools=TOOLS,
        )["conversationState"]

        self.assertEqual(len(state["history"]), 2)
        self.assertEqual(set(state["history"][0]), {"userInputMessage"})
        self.assertEqual(set(state["history"][1]), {"assistantResponseMessage"})
        current = state["currentMessage"]["userInputMessage"]
        self.assertEqual(current["content"], "Run pwd")
        self.assertIn("tools", current["userInputMessageContext"])


    def test_openai_data_url_image_is_converted_to_kiro_bytes(self):
        state = _build_conversation_state(
            "gpt-5.6-sol",
            [{"role": "user", "content": [
                {"type": "text", "text": "What is shown?"},
                {"type": "image_url", "image_url": {
                    "url": "data:image/webp;base64,UklGRg==",
                }},
            ]}],
        )["conversationState"]

        current = state["currentMessage"]["userInputMessage"]
        self.assertEqual(current["content"], "What is shown?")
        self.assertEqual(current["images"], [{
            "format": "webp",
            "source": {"bytes": "UklGRg=="},
        }])

    def test_images_are_not_repeated_in_kiro_history(self):
        state = _build_conversation_state(
            "gpt-5.6-sol",
            [
                {"role": "user", "content": [
                    {"type": "text", "text": "First image"},
                    {"type": "image_url", "image_url": "data:image/png;base64,iVBORw0KGgo="},
                ]},
                {"role": "assistant", "content": "I saw it."},
                {"role": "user", "content": "Follow-up without image"},
            ],
        )["conversationState"]

        self.assertNotIn("images", state["history"][0]["userInputMessage"])
        self.assertNotIn("images", state["currentMessage"]["userInputMessage"])

    def test_remote_image_url_is_rejected_instead_of_fetched(self):
        with self.assertRaisesRegex(ValueError, "base64 data URLs"):
            _build_conversation_state(
                "gpt-5.6-sol",
                [{"role": "user", "content": [{
                    "type": "image_url",
                    "image_url": {"url": "https://example.com/private.png"},
                }]}],
            )


class StreamingProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_tool_use_events_keep_one_openai_index(self):
        import kiro_gateway
        from unittest.mock import patch

        class Response:
            status_code = 200

        class StreamContext:
            async def __aenter__(self):
                return Response()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class Client:
            def stream(self, *args, **kwargs):
                return StreamContext()

        async def events(_response):
            for event in [
                {"name": "terminal", "toolUseId": "tooluse_one", "input": ""},
                {"name": "terminal", "toolUseId": "tooluse_one", "input": '{"command"'},
                {"name": "terminal", "toolUseId": "tooluse_one", "input": ': "pwd"}'},
                {"stop": True},
            ]:
                yield event

        with patch.object(kiro_gateway, "read_aws_eventstream", events):
            lines = [line async for line in kiro_gateway._stream_runtime(
                Client(), "token", {"conversationState": {}}, "claude-sonnet-5"
            )]

        chunks = [
            json.loads(line.removeprefix("data: ").strip())
            for line in lines
            if line.startswith("data: {")
        ]
        tool_deltas = [
            tool_call
            for chunk in chunks
            for tool_call in chunk.get("choices", [{}])[0].get("delta", {}).get("tool_calls", [])
        ]
        self.assertTrue(tool_deltas)
        self.assertEqual({delta["index"] for delta in tool_deltas}, {0})
        self.assertEqual(sum("id" in delta for delta in tool_deltas), 1)
        self.assertEqual(sum("name" in delta.get("function", {}) for delta in tool_deltas), 1)
        arguments = "".join(delta.get("function", {}).get("arguments", "") for delta in tool_deltas)
        self.assertEqual(json.loads(arguments), {"command": "pwd"})
        self.assertEqual(len({chunk["id"] for chunk in chunks}), 1)
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "tool_calls")


if __name__ == "__main__":
    unittest.main(verbosity=2)
