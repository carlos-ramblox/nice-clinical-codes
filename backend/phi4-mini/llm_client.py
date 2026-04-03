#import libraries
import os
import functools
from pathlib import Path
from dotenv import load_dotenv
from ollama import Client
import pandas as pd

load_dotenv()

def build_prompt(condition: str) -> list:
    """Build the structured prompt for ollama call"""
    system_content = ("You are a clinical coding assistant.\n\n"
        "TASK:\n"
        "Return SNOMED CT and ICD-10 codes for a condition.\n\n"
        "OUTPUT RULES:\n"
        "1. Output ONLY valid JSON.\n"
        "2. Output MUST be a JSON array.\n"
        "3. Each element MUST be an object with EXACTLY these fields:\n"
        '   "code": string,\n'
        '   "term": string,\n'
        '   "vocabulary": string,\n'
        '   "Decision": null,\n'
        '   "Confidence": null,\n'
        '   "rationale": null,\n'
        '   "sources": ["Phi-4-mini"],\n'
        '   "classifier_score": null,\n'
        '   "llm_score": null,\n'
        '   "usage_frequency": null\n'
        "4. vocabulary MUST be either 'SNOMED CT' or 'ICD-10'.\n"
        "5. Do NOT group by vocabulary.\n"
        "6. Do NOT use nested objects.\n"
        "7. Do NOT include any text outside JSON.\n\n"
        "FORMAT EXAMPLE:\n"
        "[\n"
        "  {\n"
        '    "code": "E11.9",\n'
        '    "term": "Type 2 diabetes mellitus without complications",\n'
        '    "vocabulary": "ICD-10",\n'
        '    "Decision": null,\n'
        '    "Confidence": null,\n'
        '    "rationale": null,\n'
        '    "sources": ["Phi-4-mini"],\n'
        '    "classifier_score": null,\n'
        '    "llm_score": null,\n'
        '    "usage_frequency": null\n'
        "  }\n"
        "]"
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"Condition: {condition}"},
    ]

def run_llm_chat(messages: list, client=None) -> str:
    """Run the LLM chat and return cleaned JSON string.
    Model and temperature read from .env."""
    model = os.getenv("OLLAMA_MODEL")
    temperature = os.getenv("OLLAMA_TEMPERATURE")

    if not model or not temperature:
        raise EnvironmentError(
            "OLLAMA_MODEL and OLLAMA_TEMPERATURE must be set in your .env file. "
            "See .env.example for reference."
        )

    temperature = float(temperature)

    if client is None:
        client = Client()

    response = client.chat(
      model=model,
      messages=messages,
      options={"temperature": temperature},
    )

    content = response.message.content.strip()

    # Strip code fences if the model wraps output in ```json ... ```
    if content.startswith("```"):
        content = content.strip("`")
        content = content.replace("json", "", 1).strip()

    return content

def save_json_output(func):
    """
    Decorator for saving JSON output to BASELINE_OUTPUT_DIR (configured in .env).
    """
    @functools.wraps(func)
    def wrapper(condition: str, *args, **kwargs):
        output = func(condition, *args, **kwargs)

        #normalise to top level dir
        repo_root = Path(__file__).resolve().parent.parent
        output_dir = repo_root / os.getenv("BASELINE_OUTPUT_DIR", "output-json")
        output_dir.mkdir(parents=True, exist_ok=True)

        #get timestamp
        date_today = str(pd.Timestamp.today().strftime("%Y%m%d-%H%M%S"))

        filename = condition.strip().lower().replace(" ", "_") + "-" + date_today + ".json"
        filepath = output_dir / filename

        #dump json to filepath
        with open(filepath, "w") as f:
            f.write(output)

        print(f"Saved to {filepath}")
        return output

    return wrapper



