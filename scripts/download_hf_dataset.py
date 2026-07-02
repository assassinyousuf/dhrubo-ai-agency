"""Real-World Dataset Downloader for Dhrubo AI Agency.

Downloads a 100% human-generated open-source dataset (databricks-dolly-15k)
and maps it into the OpenAI Chat schema (`messages` array) so that
fine_tune_unsloth.py can train the SLM on actual human responses to 
improve tone and prevent model collapse from synthetic LLM data.
"""

import json
from pathlib import Path
from datasets import load_dataset
from dhrubo.core.logger import get_logger

_log = get_logger("dataset_downloader")

DATASET_REPO = "databricks/databricks-dolly-15k"
OUTPUT_FILE = Path("dataset/real_training_data.jsonl")

def main():
    _log.info(f"Downloading {DATASET_REPO} from HuggingFace...")
    dataset = load_dataset(DATASET_REPO, split="train")
    
    # We filter for categories relevant to our agency: "brainstorming", "creative_writing", "information_extraction"
    relevant_categories = {"brainstorming", "creative_writing", "information_extraction"}
    
    _log.info("Filtering and formatting dataset to OpenAI Chat Schema...")
    formatted_data = []
    
    for row in dataset:
        if row["category"] not in relevant_categories:
            continue
            
        instruction = row["instruction"]
        context = row.get("context", "")
        response = row["response"]
        
        user_msg = instruction
        if context:
            user_msg = f"{instruction}\n\nContext:\n{context}"
            
        formatted_data.append({
            "messages": [
                {"role": "system", "content": "You are a professional AI Agency analyst and copywriter. Respond accurately and thoughtfully."},
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": response}
            ]
        })
        
    _log.info(f"Converted {len(formatted_data)} human-written examples.")
    
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for item in formatted_data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            
    _log.info(f"Successfully saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
