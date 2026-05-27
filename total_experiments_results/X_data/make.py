import os
import json
import pandas as pd

## RAW 실험 데이터 그대로 추출 ##
## 스크립트 작업시, 전처리 필요할 수 있음 ##
## methods별 sample-0, sample-1, sample-2 다른 데이터로 인지 ##

HERE = os.path.dirname(os.path.abspath(__file__))
langs = ["cpp", "go", "java", "js", "python"]

# 1. Method 및 Metric 매핑 딕셔너리
basic_method_map = {
    "1-1": "V-BIN",
    "1-0": "V-BIN w/o r",
    "1-9": "V-Score",
    "1-8": "V-Score w/o r",
    "1-3": "ICE-Score",
    "1-2": "ICE-Score w/o r",
    "2-0-1": "CJ-BIN",
    "2-0-0": "CJ-BIN w/o r",
    "1-5": "CJ-Score",
    "1-4": "CJ-Score w/o r",
}

reference_mapping = {
    "bleu": "BLEU",
    "codebleu": "CodeBLEU",
    "chrf": "chrF",
    "rougel": "ROUGE-L",
    "ruby": "RUBY",
    "meteor": "METEOR",
    "code_bert_score_f1": "CBS_F1",
    "code_bert_score_f3": "CBS_F3",
}

columns_order = [
    "LANG", "TASK_ID","SAMPLE_ID", "EXECUTION_BASE_BINARY", "EXECUTION_BASE_RATIO",
    "BLEU", "ROUGE-L", "METEOR", "chrF", "CodeBLEU", "RUBY", "CBS_F1", "CBS_F3",
    "ICE-Score", "ICE-Score w/o r", "CJ-BIN", "CJ-BIN w/o r", "CJ-Score", "CJ-Score w/o r",
    "V-BIN", "V-BIN w/o r", "V-Score", "V-Score w/o r"
]

all_data = []
missing_files = []
duplicate_warnings = []

# 3. 언어별 데이터 추출 진행
for l in langs:
    data_store = {}

    def get_record(tid, sid):
        key = (str(tid), int(sid))
        if key not in data_store:
            data_store[key] = {col: None for col in columns_order}
            data_store[key]["LANG"] = l
            data_store[key]["TASK_ID"] = str(tid)
            data_store[key]["SAMPLE_ID"] = int(sid)
        return data_store[key]

    
    for s in range(3):
        ratio_file = os.path.join(HERE, "execution_score", f"humaneval_{l}_codejudge_base_ratio.json")
        if os.path.exists(ratio_file):
            with open(ratio_file, 'r', encoding='utf-8') as f:
                ratio_data = json.load(f)
                for tid, score in ratio_data.items():
                    record = get_record(tid, s)
                    record["EXECUTION_BASE_RATIO"] = score
                    record["EXECUTION_BASE_BINARY"] = 1.0 if score == 1.0 else 0.0

    for s in range(3):
        ref_file = os.path.join(HERE, "methods_score", l, f"other-metrics-without-prefix-sample-{s}.json")
        if os.path.exists(ref_file):
            with open(ref_file, 'r', encoding='utf-8') as f:
                ref_data = json.load(f)
                for item in ref_data:
                    tid = item.get("question_id")
                    if not tid: continue
                    record = get_record(tid, s)
                    for origin_key, target_col in reference_mapping.items():
                        if origin_key in item:
                            record[target_col] = item[origin_key]
        else:
            missing_files.append(ref_file)

    for method_code, col_name in basic_method_map.items():
        for s in range(3):
            llm_file = os.path.join(HERE, "methods_score", l, f"gpt-3.5-turbo-1106-{method_code}-0.0-sample-{s}.json")
            if os.path.exists(llm_file):
                with open(llm_file, 'r', encoding='utf-8') as f:
                    llm_data = json.load(f)
                    for item in llm_data.get("data", []):
                        tid = item.get("question_id")
                        if not tid: continue
                        
                        record = get_record(tid, s)
                        score_obj = item.get("code_gpt_score", {})
                        if "code_gpt_score" in score_obj:
                            record[col_name] = score_obj["code_gpt_score"]
            else:
                missing_files.append(llm_file)

    all_data.extend(data_store.values())

df = pd.DataFrame(all_data)
metric_columns = [
    "BLEU", "ROUGE-L", "METEOR", "chrF", "CodeBLEU", "RUBY", "CBS_F1", "CBS_F3",
    "ICE-Score", "ICE-Score w/o r", "CJ-BIN", "CJ-BIN w/o r", "CJ-Score", "CJ-Score w/o r",
    "V-BIN", "V-BIN w/o r", "V-Score", "V-Score w/o r"
]
df = df.dropna(subset=metric_columns, how='all')

for col in columns_order:
    if col not in df.columns:
        df[col] = pd.NA

df = df[columns_order]

# 5. CSV 및 Parquet 파일 저장
csv_out = os.path.join(HERE, "HumanEval_X_Total_Results.csv")
parquet_out = os.path.join(HERE, "HumanEval_X_Total_Results.parquet")

df.to_csv(csv_out, index=False, encoding='utf-8')
df.to_parquet(parquet_out, index=False)

# 결과 요약 리포트
print("\n" + "="*60)
print(f"Data aggregation complete!")
print(f"- Total rows extracted: {len(df)}")
print(f"- Saved CSV: {csv_out}")
print(f"- Saved Parquet: {parquet_out}")
print("="*60)

# 누락된 파일 요약
if missing_files:
    print(f"\n[!] 누락된 파일 ({len(missing_files)}건):")
    for mf in missing_files:
        print(f"  - {mf}")
else:
    print("\n[+] 누락된 파일이 없습니다.")

# 중복 데이터 요약
if duplicate_warnings:
    print(f"\n[!] 중복 데이터 ({len(duplicate_warnings)}건):")
    for dw in duplicate_warnings:
        print(f"  - {dw}")
else:
    print("\n[+] 각 파일 내 question_id 중복이 없습니다.")