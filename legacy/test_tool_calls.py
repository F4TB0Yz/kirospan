#!/usr/bin/env python3
"""
Test script to verify Kiro gateway handles tool calls correctly.
"""
import json
import sys

# Test 1: Parse Kiro event with tool use
from kiro_gateway import _parse_kiro_event, _parse_bracket_tool_calls

def test_parse_tool_use_event():
    """Test parsing a tool use event from Kiro."""
    kiro_event = {
        "name": "bash",
        "toolUseId": "tool_abc123",
        "input": {"command": "ls -la"}
    }

    result = _parse_kiro_event(kiro_event)
    assert result is not None, "Should parse tool use event"
    assert result["type"] == "toolUse", f"Expected toolUse, got {result['type']}"
    assert result["data"]["name"] == "bash", f"Expected bash, got {result['data']['name']}"
    assert result["data"]["toolUseId"] == "tool_abc123", f"Expected tool_abc123, got {result['data']['toolUseId']}"
    print("✓ Test 1: Parse tool use event - PASSED")

def test_parse_tool_use_input():
    """Test parsing tool use input streaming."""
    kiro_event = {
        "input": '{"command": "ls -la"}'
    }

    result = _parse_kiro_event(kiro_event)
    assert result is not None, "Should parse tool use input"
    assert result["type"] == "toolUseInput", f"Expected toolUseInput, got {result['type']}"
    assert result["data"]["input"] == '{"command": "ls -la"}', f"Input mismatch: {result['data']['input']}"
    print("✓ Test 2: Parse tool use input streaming - PASSED")

def test_parse_content_event():
    """Test parsing regular content event."""
    kiro_event = {
        "content": "Hello, I'm processing your request."
    }

    result = _parse_kiro_event(kiro_event)
    assert result is not None, "Should parse content event"
    assert result["type"] == "content", f"Expected content, got {result['type']}"
    assert result["data"] == "Hello, I'm processing your request.", f"Content mismatch: {result['data']}"
    print("✓ Test 3: Parse content event - PASSED")

def test_parse_usage_event():
    """Test parsing usage event."""
    kiro_event = {
        "usage": {
            "inputTokens": 150,
            "outputTokens": 50
        }
    }

    result = _parse_kiro_event(kiro_event)
    assert result is not None, "Should parse usage event"
    assert result["type"] == "usage", f"Expected usage, got {result['type']}"
    assert result["data"]["inputTokens"] == 150, f"Input tokens mismatch: {result['data']['inputTokens']}"
    assert result["data"]["outputTokens"] == 50, f"Output tokens mismatch: {result['data']['outputTokens']}"
    print("✓ Test 4: Parse usage event - PASSED")

def test_parse_error_event():
    """Test parsing error event."""
    kiro_event = {
        "error": "Rate limit exceeded",
        "message": "Please retry after 60 seconds"
    }

    result = _parse_kiro_event(kiro_event)
    assert result is not None, "Should parse error event"
    assert result["type"] == "error", f"Expected error, got {result['type']}"
    assert result["data"]["error"] == "Rate limit exceeded", f"Error mismatch: {result['data']['error']}"
    print("✓ Test 5: Parse error event - PASSED")

def test_parse_bracket_tool_calls():
    """Test parsing bracket-style tool calls."""
    text = '''I'll help you with that. [Called bash with args: {"command": "ls -la"}]'''

    tool_calls, cleaned = _parse_bracket_tool_calls(text)

    assert len(tool_calls) == 1, f"Expected 1 tool call, got {len(tool_calls)}"
    assert tool_calls[0]["function"]["name"] == "bash", f"Expected bash, got {tool_calls[0]['function']['name']}"
    assert "I'll help you with that." in cleaned, f"Cleaned text should contain original text without tool call"
    assert "[Called bash" not in cleaned, f"Cleaned text should not contain bracket tool call"
    print("✓ Test 6: Parse bracket tool calls - PASSED")

def test_parse_multiple_bracket_tool_calls():
    """Test parsing multiple bracket-style tool calls."""
    text = '''Let me check. [Called bash with args: {"command": "ls"}] and [Called python with args: {"script": "print('hi')"}]'''

    tool_calls, cleaned = _parse_bracket_tool_calls(text)

    assert len(tool_calls) == 2, f"Expected 2 tool calls, got {len(tool_calls)}"
    assert tool_calls[0]["function"]["name"] == "bash", f"First tool should be bash"
    assert tool_calls[1]["function"]["name"] == "python", f"Second tool should be python"
    print("✓ Test 7: Parse multiple bracket tool calls - PASSED")

def test_unknown_event():
    """Test that unknown events return None."""
    kiro_event = {
        "unknown_field": "some value"
    }

    result = _parse_kiro_event(kiro_event)
    assert result is None, "Unknown events should return None"
    print("✓ Test 8: Unknown event returns None - PASSED")

def test_tool_use_with_dict_input():
    """Test tool use with dictionary input (should be JSON stringified)."""
    kiro_event = {
        "name": "bash",
        "toolUseId": "tool_xyz",
        "input": {"command": "ls -la", "timeout": 30}
    }

    result = _parse_kiro_event(kiro_event)
    assert result is not None, "Should parse tool use with dict input"
    assert result["type"] == "toolUse", f"Expected toolUse, got {result['type']}"
    # Input should be JSON stringified
    import json
    parsed_input = json.loads(result["data"]["input"])
    assert parsed_input["command"] == "ls -la", f"Command mismatch: {parsed_input['command']}"
    assert parsed_input["timeout"] == 30, f"Timeout mismatch: {parsed_input['timeout']}"
    print("✓ Test 9: Tool use with dict input - PASSED")

if __name__ == "__main__":
    print("Running Kiro Gateway Tool Call Tests\n" + "=" * 50)

    try:
        test_parse_tool_use_event()
        test_parse_tool_use_input()
        test_parse_content_event()
        test_parse_usage_event()
        test_parse_error_event()
        test_parse_bracket_tool_calls()
        test_parse_multiple_bracket_tool_calls()
        test_unknown_event()
        test_tool_use_with_dict_input()

        print("\n" + "=" * 50)
        print("✓ ALL TESTS PASSED")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ TEST ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
