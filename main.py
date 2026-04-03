import argparse
from dotenv import load_dotenv
from llm_client import build_prompt, run_llm_chat, save_json_output

#load config
load_dotenv()

@save_json_output
def query_llm(condition: str) -> str:
    """Query phi4 for SNOMED/ICD-10 codes"""
    messages = build_prompt(condition)

    return run_llm_chat(messages)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Condition of interest for baseline LLM query")
    parser.add_argument("--condition", type=str, required=True, help="condition of interest")
    args = parser.parse_args()

    print(f"Querying Phi4 for SNOMED/ICD-10 codes relating to {args.condition}")
    result = query_llm(args.condition)
    print(result)





