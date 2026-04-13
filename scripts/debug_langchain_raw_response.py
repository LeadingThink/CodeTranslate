from __future__ import annotations

import argparse
import json

from langchain_openai import ChatOpenAI

from codetranslate.core.settings import AppSettings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Debug raw LangChain model output without structured parsing.")
    parser.add_argument("--prompt", required=True, help="User prompt sent to the model.")
    parser.add_argument("--system", default="You are a helpful assistant.", help="System prompt.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = AppSettings.from_env()
    if not settings.has_api_key:
        raise RuntimeError("Missing API key. Set CODETRANSLATE_API_KEY or OPENAI_API_KEY first.")

    model = ChatOpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.model_name,
        temperature=0,
    )

    response = model.invoke(
        [
            {"role": "system", "content": args.system},
            {"role": "user", "content": args.prompt},
        ]
    )

    print("=== RAW CONTENT ===")
    print(response.content)
    print("=== RESPONSE TYPE ===")
    print(type(response.content).__name__)
    print("=== ADDITIONAL_KWARGS ===")
    print(json.dumps(response.additional_kwargs, indent=2, ensure_ascii=False, default=str))
    print("=== RESPONSE_METADATA ===")
    print(json.dumps(response.response_metadata, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
