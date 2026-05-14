import json
import pytest
from drissionpage_mcp.server import _js_error_details

def test_js_error_categorization_plain_string():
    script = "console.log(x);"

    # ReferenceError
    exc = Exception("ReferenceError: x is not defined")
    details = _js_error_details(script, exc)
    assert details["error_kind"] == "reference"

    # TypeError
    exc = Exception("TypeError: null is not an object")
    details = _js_error_details(script, exc)
    assert details["error_kind"] == "type"

    # RangeError
    exc = Exception("RangeError: Invalid array length")
    details = _js_error_details(script, exc)
    assert details["error_kind"] == "range"

    # SyntaxError
    exc = Exception("SyntaxError: Unexpected token")
    details = _js_error_details(script, exc)
    assert details["error_kind"] == "syntax"

def test_js_error_categorization_json():
    script = "return x.y;"

    # ReferenceError in JSON
    exc = Exception(json.dumps({
        "exception": {
            "description": "ReferenceError: x is not defined",
            "className": "ReferenceError"
        },
        "lineNumber": 0,
        "columnNumber": 7
    }))
    details = _js_error_details(script, exc)
    assert details["error_kind"] == "reference"
    assert details["line"] == 1
    assert details["column"] == 8

    # TypeError in JSON
    exc = Exception(json.dumps({
        "exception": {
            "description": "TypeError: Cannot read property 'y' of undefined",
            "className": "TypeError"
        }
    }))
    details = _js_error_details(script, exc)
    assert details["error_kind"] == "type"

    # SyntaxError in JSON
    exc = Exception(json.dumps({
        "exceptionId": 1,
        "text": "Uncaught",
        "lineNumber": 0,
        "columnNumber": 0,
        "exception": {
            "description": "SyntaxError: Unexpected identifier",
            "value": "SyntaxError: Unexpected identifier"
        }
    }))
    details = _js_error_details(script, exc)
    assert details["error_kind"] == "syntax"

def test_js_error_fallback():
    script = "throw new Error('custom')"
    exc = Exception("Error: custom")
    details = _js_error_details(script, exc)
    assert details["error_kind"] == "runtime"

def test_js_error_syntax_patterns():
    script = "if(true){"
    patterns = ["unexpected token", "parse error", "unterminated string"]
    for p in patterns:
        exc = Exception(f"Some message with {p}")
        details = _js_error_details(script, exc)
        assert details["error_kind"] == "syntax"
