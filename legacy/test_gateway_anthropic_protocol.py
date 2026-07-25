#!/usr/bin/env python3
"""Regression tests for Claude Code / Anthropic protocol compatibility."""

import json
import unittest
from unittest.mock import patch

from kiro_gateway import (
    AnthropicRequest,
    _anthropic_response_from_openai,
    _anthropic_stream_runtime,
    _build_conversation_state,
    _convert_anthropic_request,
    _normalize_model_id,
)


class ClaudeCodeModelAliasTests(unittest.TestCase):
    def test_fable_5_maps_to_kiro_gpt_5_6_sol(self):
        self.assertEqual(_normalize_model_id("claude-fable-5"), "gpt-5.6-sol")

    def test_opus_4_8_dash_id_maps_to_kiro_dotted_id(self):
        self.assertEqual(_normalize_model_id("claude-opus-4-8"), "claude-opus-4.8")

    def test_claude_code_1m_context_modifier_is_removed_before_fable_alias(self):
        self.assertEqual(_normalize_model_id("claude-fable-5[1m]"), "gpt-5.6-sol")

    def test_claude_code_1m_context_modifier_is_removed_before_opus_alias(self):
        self.assertEqual(_normalize_model_id("claude-opus-4-8[1m]"), "claude-opus-4.8")

    def test_real_ansi_sequences_and_dangling_sgr_fragment_are_removed(self):
        self.assertEqual(
            _normalize_model_id("\x1b[1mclaude-opus-4-8\x1b[0m"),
            "claude-opus-4.8",
        )
        self.assertEqual(_normalize_model_id("claude-fable-5[22m"), "gpt-5.6-sol")

    def test_valid_kiro_model_is_preserved(self):
        self.assertEqual(_normalize_model_id("claude-sonnet-5"), "claude-sonnet-5")


class AnthropicRequestConversionTests(unittest.TestCase):
    def test_system_tools_and_user_message_are_converted(self):
        request = AnthropicRequest(
            model="claude-sonnet-5",
            system=[{"type": "text", "text": "Be precise."}],
            messages=[{"role": "user", "content": [{"type": "text", "text": "Run pwd"}]}],
            tools=[{
                "name": "Bash",
                "description": "Run a command",
                "input_schema": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            }],
        )

        messages, tools = _convert_anthropic_request(request)

        self.assertEqual(messages[0], {"role": "system", "content": "Be precise."})
        self.assertEqual(messages[1], {"role": "user", "content": "Run pwd"})
        self.assertEqual(tools[0]["function"]["name"], "Bash")
        self.assertEqual(tools[0]["function"]["parameters"]["required"], ["command"])

    def test_tool_use_and_result_are_converted(self):
        request = AnthropicRequest(
            model="claude-sonnet-5",
            messages=[
                {"role": "user", "content": "Run pwd"},
                {"role": "assistant", "content": [{
                    "type": "tool_use",
                    "id": "toolu_123",
                    "name": "Bash",
                    "input": {"command": "pwd"},
                }]},
                {"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu_123",
                    "content": "/workspace/project",
                }]},
            ],
        )

        messages, _ = _convert_anthropic_request(request)

        self.assertEqual(messages[1]["tool_calls"][0]["id"], "toolu_123")
        self.assertEqual(
            json.loads(messages[1]["tool_calls"][0]["function"]["arguments"]),
            {"command": "pwd"},
        )
        self.assertEqual(messages[2], {
            "role": "tool",
            "tool_call_id": "toolu_123",
            "content": "/workspace/project",
            "is_error": False,
        })

    def test_failed_anthropic_tool_result_becomes_kiro_error_status(self):
        request = AnthropicRequest(
            model="claude-opus-4-8",
            messages=[
                {"role": "user", "content": "Write a plan"},
                {"role": "assistant", "content": [{
                    "type": "tool_use",
                    "id": "toolu_write",
                    "name": "Write",
                    "input": {"file_path": "/tmp/plan.md", "content": "plan"},
                }]},
                {"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu_write",
                    "is_error": True,
                    "content": "No such tool available: Write",
                }]},
            ],
        )

        messages, tools = _convert_anthropic_request(request)
        self.assertTrue(messages[-1]["is_error"])
        state = _build_conversation_state("claude-opus-4.8", messages, tools=tools)["conversationState"]
        result = state["currentMessage"]["userInputMessage"]["userInputMessageContext"]["toolResults"][0]
        self.assertEqual(result["status"], "error")
        specifications = [
            tool["toolSpecification"]
            for tool in state["currentMessage"]["userInputMessage"]["userInputMessageContext"]["tools"]
        ]
        write_tool = next(spec for spec in specifications if spec["name"] == "Write")
        self.assertEqual(
            write_tool["inputSchema"]["json"],
            {"type": "object", "properties": {}},
        )

    def test_parallel_tool_results_with_skill_text_stay_on_current_kiro_message(self):
        request = AnthropicRequest(
            model="claude-fable-5",
            messages=[
                {"role": "user", "content": "Load skills"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "toolu_one", "name": "Skill", "input": {"skill": "one"}},
                    {"type": "tool_use", "id": "toolu_two", "name": "Skill", "input": {"skill": "two"}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_one", "content": "Successfully loaded skill"},
                    {"type": "text", "text": "Skill body one"},
                    {"type": "tool_result", "tool_use_id": "toolu_two", "content": "Successfully loaded skill"},
                    {"type": "text", "text": "Skill body two"},
                ]},
            ],
        )

        messages, tools = _convert_anthropic_request(request)
        state = _build_conversation_state("gpt-5.6-sol", messages, tools=tools)["conversationState"]
        current = state["currentMessage"]["userInputMessage"]

        self.assertEqual(current["content"], "Skill body one\nSkill body two")
        results = current["userInputMessageContext"]["toolResults"]
        self.assertEqual([result["toolUseId"] for result in results], ["toolu_one", "toolu_two"])
        self.assertEqual(len(state["history"]), 2)

    def test_anthropic_tool_result_includes_persistence_reminder(self):
        request = AnthropicRequest(
            model="claude-fable-5",
            messages=[
                {"role": "user", "content": "Run implementer then reviewer"},
                {"role": "assistant", "content": [{
                    "type": "tool_use",
                    "id": "toolu_agent",
                    "name": "Agent",
                    "input": {"description": "Implement"},
                }]},
                {"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu_agent",
                    "content": "Implementation complete",
                }]},
            ],
        )

        messages, tools = _convert_anthropic_request(request)
        state = _build_conversation_state(
            "gpt-5.6-sol",
            messages,
            tools=tools,
            enforce_tool_continuation=True,
        )["conversationState"]
        content = state["currentMessage"]["userInputMessage"]["content"]

        self.assertIn("Do not end the turn after describing a next step", content)
        self.assertIn("call it in this response", content)


class AnthropicStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_kiro_usage_is_forwarded_to_anthropic_message_delta(self):
        async def openai_stream():
            yield 'data: {"choices":[{"delta":{"content":"Done"},"finish_reason":null}]}\n\n'
            yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":21,"completion_tokens":7,"total_tokens":28}}\n\n'
            yield "data: [DONE]\n\n"

        with patch("kiro_gateway._stream_runtime", return_value=openai_stream()):
            lines = [
                line async for line in _anthropic_stream_runtime(
                    client=None,
                    access_token="token",
                    state={},
                    model_id="claude-fable-5",
                )
            ]

        events = [
            json.loads(line.split("data: ", 1)[1])
            for line in lines
            if line.startswith("event: message_delta")
        ]
        self.assertEqual(events[0]["usage"]["output_tokens"], 7)


    async def test_missing_kiro_usage_is_estimated_from_streamed_output(self):
        async def openai_stream():
            yield 'data: {"choices":[{"delta":{"content":"Four token-ish words"},"finish_reason":null}]}\n\n'
            yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}}\n\n'
            yield "data: [DONE]\n\n"

        with patch("kiro_gateway._stream_runtime", return_value=openai_stream()):
            lines = [
                line async for line in _anthropic_stream_runtime(
                    client=None,
                    access_token="token",
                    state={},
                    model_id="claude-fable-5",
                )
            ]

        events = [
            json.loads(line.split("data: ", 1)[1])
            for line in lines
            if line.startswith("event: message_delta")
        ]
        self.assertGreater(events[0]["usage"]["output_tokens"], 0)


    async def test_progress_promise_after_parallel_results_auto_continues_to_tool_call(self):
        async def premature_stop():
            yield 'data: {"choices":[{"delta":{"content":"Feature READY, pero corregiré responsividad, resumen obsoleto y render duplicado antes del cierre."},"finish_reason":null}]}\n\n'
            yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":0,"completion_tokens":23,"total_tokens":23}}\n\n'
            yield "data: [DONE]\n\n"

        async def continued_tool_call():
            yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"toolu_edit","type":"function","function":{"name":"Edit","arguments":"{\\"file_path\\":\\"/tmp/safe\\"}"}}]},"finish_reason":null}]}\n\n'
            yield 'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":0,"completion_tokens":11,"total_tokens":11}}\n\n'
            yield "data: [DONE]\n\n"

        state = {
            "profileArn": "arn:test",
            "agentMode": "vibe",
            "conversationState": {
                "conversationId": "conversation-test",
                "chatTriggerType": "MANUAL",
                "agentTaskType": "vibe",
                "currentMessage": {"userInputMessage": {
                    "content": "Tool results provided.\n\nDo not end the turn after describing a next step.",
                    "modelId": "gpt-5.6-sol",
                    "origin": "KIRO_CLI",
                    "userInputMessageContext": {
                        "toolResults": [
                            {"toolUseId": f"agent-{index}", "status": "success", "content": [{"text": "review"}]}
                            for index in range(5)
                        ],
                        "tools": [{"toolSpecification": {
                            "name": "Edit",
                            "description": "Edit",
                            "inputSchema": {"json": {"type": "object", "properties": {}}},
                        }}],
                    },
                }},
            },
        }

        with patch(
            "kiro_gateway._stream_runtime",
            side_effect=[premature_stop(), continued_tool_call()],
        ) as runtime:
            lines = [
                line async for line in _anthropic_stream_runtime(
                    client=None,
                    access_token="token",
                    state=state,
                    model_id="claude-fable-5",
                )
            ]

        self.assertEqual(runtime.call_count, 2)
        self.assertEqual(sum(line.startswith("event: message_stop") for line in lines), 1)
        message_deltas = [
            json.loads(line.split("data: ", 1)[1])
            for line in lines
            if line.startswith("event: message_delta")
        ]
        self.assertEqual(message_deltas[0]["delta"]["stop_reason"], "tool_use")
        self.assertEqual(message_deltas[0]["usage"]["output_tokens"], 34)
        starts = [
            json.loads(line.split("data: ", 1)[1])
            for line in lines
            if line.startswith("event: content_block_start")
        ]
        self.assertTrue(any(event["content_block"].get("name") == "Edit" for event in starts))
        continuation_state = runtime.call_args_list[1].args[2]
        history = continuation_state["conversationState"]["history"]
        self.assertIn("corregiré responsividad", history[-1]["assistantResponseMessage"]["content"])
        self.assertNotIn(
            "toolResults",
            continuation_state["conversationState"]["currentMessage"]["userInputMessage"].get(
                "userInputMessageContext", {}
            ),
        )


    def test_base64_image_is_preserved_for_current_kiro_message(self):
        image_data = "iVBORw0KGgo="
        request = AnthropicRequest(
            model="claude-sonnet-5",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "Describe this image"},
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_data,
                }},
            ]}],
        )

        messages, tools = _convert_anthropic_request(request)
        state = _build_conversation_state("claude-sonnet-5", messages, tools=tools)["conversationState"]
        current = state["currentMessage"]["userInputMessage"]

        self.assertEqual(current["content"], "Describe this image")
        self.assertEqual(current["images"], [{
            "format": "png",
            "source": {"bytes": image_data},
        }])

    def test_image_in_tool_result_is_attached_to_current_kiro_message(self):
        image_data = "R0lGODlh"
        request = AnthropicRequest(
            model="claude-sonnet-5",
            messages=[
                {"role": "user", "content": "Read the image"},
                {"role": "assistant", "content": [{
                    "type": "tool_use",
                    "id": "toolu_read_image",
                    "name": "Read",
                    "input": {"file_path": "/tmp/image.gif"},
                }]},
                {"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu_read_image",
                    "content": [
                        {"type": "text", "text": "Image Size: 10x10"},
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": "image/gif",
                            "data": image_data,
                        }},
                    ],
                }]},
            ],
        )

        messages, tools = _convert_anthropic_request(request)
        state = _build_conversation_state("claude-sonnet-5", messages, tools=tools)["conversationState"]
        current = state["currentMessage"]["userInputMessage"]

        self.assertEqual(current["images"][0]["format"], "gif")
        self.assertEqual(current["images"][0]["source"]["bytes"], image_data)
        self.assertEqual(
            current["userInputMessageContext"]["toolResults"][0]["content"],
            [{"text": "Image Size: 10x10"}],
        )


class AnthropicResponseConversionTests(unittest.TestCase):
    def test_openai_tool_call_becomes_anthropic_tool_use(self):
        response = _anthropic_response_from_openai({
            "id": "chatcmpl_1",
            "model": "claude-sonnet-5",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Checking",
                    "tool_calls": [{
                        "id": "toolu_123",
                        "type": "function",
                        "function": {"name": "Bash", "arguments": '{"command":"pwd"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

        self.assertEqual(response["type"], "message")
        self.assertEqual(response["stop_reason"], "tool_use")
        self.assertEqual(response["content"][0], {"type": "text", "text": "Checking"})
        self.assertEqual(response["content"][1]["type"], "tool_use")
        self.assertEqual(response["content"][1]["input"], {"command": "pwd"})
        self.assertEqual(response["usage"], {"input_tokens": 10, "output_tokens": 5})


if __name__ == "__main__":
    unittest.main(verbosity=2)
