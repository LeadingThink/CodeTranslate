from __future__ import annotations

import unittest
from types import SimpleNamespace

from codetranslate.runtime.reporter import extract_token_usage


class ReporterTokenUsageTests(unittest.TestCase):
    def test_extract_token_usage_from_usage_metadata(self) -> None:
        result = {
            "messages": [
                SimpleNamespace(
                    usage_metadata={
                        "input_tokens": 120,
                        "output_tokens": 45,
                        "total_tokens": 165,
                    }
                )
            ]
        }

        usage = extract_token_usage(result)

        self.assertEqual(
            usage,
            {
                "input_tokens": 120,
                "output_tokens": 45,
                "total_tokens": 165,
            },
        )

    def test_extract_token_usage_from_response_metadata(self) -> None:
        result = {
            "messages": [
                SimpleNamespace(
                    usage_metadata=None,
                    response_metadata={
                        "token_usage": {
                            "prompt_tokens": 88,
                            "completion_tokens": 12,
                            "total_tokens": 100,
                        }
                    },
                )
            ]
        }

        usage = extract_token_usage(result)

        self.assertEqual(
            usage,
            {
                "input_tokens": 88,
                "output_tokens": 12,
                "total_tokens": 100,
            },
        )


if __name__ == "__main__":
    unittest.main()
