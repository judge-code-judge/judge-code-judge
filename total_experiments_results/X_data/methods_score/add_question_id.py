import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
lang = ["cpp", "go", "java", "js", "python"]

# canonical_solution 기준으로 question_id 매핑

for l in lang:
    modify_path = os.path.join(HERE, l, "other-metrics-without-prefix-sample-0.json")
    refer_path = os.path.join(HERE, l, "gpt-3.5-turbo-1106-1-0-0.0-sample-0.json")

    with open(modify_path, 'r', encoding='utf-8') as f:
        modify_data = json.load(f)
    
    with open(refer_path, 'r', encoding='utf-8') as f:
        refer_data = json.load(f)
        refer_list = refer_data.get("data", [])

    for item in modify_data:
        target_sol = item.get("canonical_solution", "")
    
        for ref_item in refer_list:
            ref_sol = ref_item.get("canonical_solution", "")
            
            if target_sol and target_sol in ref_sol:
                item["question_id"] = ref_item["question_id"]
                break
        else:
            item["question_id"] = None

    with open(modify_path, 'w', encoding='utf-8') as f:
        json.dump(modify_data, f, indent=4, ensure_ascii=False)

    print(f"Processed {l}: Updated {len(modify_data)} entries.")