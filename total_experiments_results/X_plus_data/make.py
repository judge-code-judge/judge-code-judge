import os
import json
import glob
import pandas as pd
from collections import defaultdict

## RAW 실험 데이터 그대로 추출 ##
## 스크립트 작업시, 전처리 필요할 수 있음 ##
## Qwen 모델의 경우 sample-0, 1, 2 반영, GPT 모델들은 sample-0만 반영 ##

HERE = os.path.dirname(os.path.abspath(__file__))
EXEC_DIR = os.path.join(HERE, "execution_score_with_refer_based_methods_score")
LLM_DIR = os.path.join(HERE, "llm_based_methods_score")

langs = ["cpp", "go", "java", "js", "python"]

# 1. Method 및 Metric 매핑 딕셔너리
# "file_name_usage" : "paper usage"
llm_method_map = {
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

# "json_data_usage" : "paper usage"
reference_mapping = {
    "bleu": "BLEU",
    "codebleu": "CodeBLEU",
    "chrf": "chrF",
    "rougel": "ROUGE-L",
    "ruby": "RUBY",
    "meteor": "METEOR",
    "codebertscore_f1": "CBS_F1",
    "codebertscore_f3": "CBS_F3",
}

# "file_name_usage" : "paper usage"
llm_model_mapping = {
    "gpt-3.5-turbo-1106": "GPT-3.5-Turbo",
    "gpt-5-nano": "GPT-5-nano",
    "Qwen3-Next-80B": "Qwen3-Next-80B",
}

# 모델별 샘플 개수 매핑 정의
llm_model_samples = {
    "gpt-3.5-turbo-1106": [0],
    "gpt-5-nano": [0],
    "Qwen3-Next-80B": [0, 1, 2],
}

# 2. 최종 출력 컬럼 순서 정의 (SAMPLE_ID 추가)
columns_order = [
    "LLM_MODEL", "LANG", "TASK_ID", "SNIPPET_ID", "SAMPLE_ID", "EXECUTION_PLUS_BINARY", "EXECUTION_PLUS_RATIO",
    "BLEU", "ROUGE-L", "METEOR", "chrF", "CodeBLEU", "RUBY", "CBS_F1", "CBS_F3",
    "ICE-Score", "ICE-Score w/o r", "CJ-BIN", "CJ-BIN w/o r", "CJ-Score", "CJ-Score w/o r",
    "V-BIN", "V-BIN w/o r", "V-Score", "V-Score w/o r"
]

def main():
    # 데이터를 담을 마스터 딕셔너리
    # 구조: master_data[(LLM_MODEL, LANG, TASK_ID, SNIPPET_ID, SAMPLE_ID)] = { 컬럼명: 값 }
    master_data = defaultdict(dict)
    
    missing_logs = []
    duplicate_logs = []

    # ==========================================
    # 1. Execution & Reference Data 파싱 (Ground Truth)
    # ==========================================
    print("--- 1. Execution & Reference Data 로드 ---")
    gt_cache = {} 
    
    for lang in langs:
        file_path = os.path.join(EXEC_DIR, f"humaneval_{lang}_grade_evalplus_ratio.json")
        if not os.path.exists(file_path):
            missing_logs.append(f"[GT_FILE_MISSING] 파일 없음: {file_path}")
            continue
            
        with open(file_path, "r", encoding="utf-8") as f:
            try:
                data_list = json.load(f)
            except json.JSONDecodeError:
                missing_logs.append(f"[GT_FILE_ERROR] JSON 파싱 에러: {file_path}")
                continue
                
        for item in data_list:
            task_id = str(item.get("task_id", ""))
            if not task_id:
                continue
                
            for key, value in item.items():
                if key.startswith("grade-"):
                    snippet_id = key.split("-")[1]
                    
                    gt_record = {
                        "EXECUTION_PLUS_RATIO": value.get("execution_plus_ratio", None)
                    }
                    
                    # Reference Score 추출
                    for raw_key, paper_key in reference_mapping.items():
                        gt_record[paper_key] = value.get(raw_key, None)
                        
                    gt_cache[(lang, task_id, snippet_id)] = gt_record

    # Ground Truth 데이터를 기반으로, 모든 LLM Model과 해당 Sample에 대한 Base 행 생성
    for (lang, task_id, snippet_id), gt_record in gt_cache.items():
        for raw_model, paper_model in llm_model_mapping.items():
            for s in llm_model_samples[raw_model]:
                row_key = (paper_model, lang, task_id, snippet_id, s)
                
                master_data[row_key].update(gt_record)
                
                # Ratio 기반 Binary 자체 계산 적용 (1.0이면 1.0, 그 외 0.0)
                ratio = gt_record.get("EXECUTION_PLUS_RATIO")
                master_data[row_key]["EXECUTION_PLUS_BINARY"] = 1.0 if ratio == 1.0 else 0.0

    # ==========================================
    # 2. LLM-Based Methods Score 파싱
    # ==========================================
    print("--- 2. LLM Based Methods Data 로드 ---")
    for lang in langs:
        for raw_model, paper_model in llm_model_mapping.items():
            for raw_method, paper_method in llm_method_map.items():
                for s in llm_model_samples[raw_model]:
                    # 와일드카드(*)를 이용해 온도 파라미터 등과 상관없이 sample-{s} 파일을 탐색
                    search_pattern = os.path.join(LLM_DIR, lang, f"{raw_model}-{raw_method}-*-sample-{s}_icedata.json")
                    matched_files = glob.glob(search_pattern)
                    
                    if not matched_files:
                        missing_logs.append(f"[LLM_FILE_MISSING] 파일 없음: {search_pattern}")
                        continue
                    
                    if len(matched_files) > 1:
                        duplicate_logs.append(f"[LLM_FILE_DUPLICATE] 패턴에 매칭되는 파일이 여러 개 존재함: {search_pattern}. 첫 번째 파일만 사용합니다.")
                    
                    file_path = matched_files[0]
                    with open(file_path, "r", encoding="utf-8") as f:
                        try:
                            llm_json = json.load(f)
                        except json.JSONDecodeError:
                            missing_logs.append(f"[LLM_FILE_ERROR] JSON 파싱 에러: {file_path}")
                            continue
                            
                    for item in llm_json.get("data", []):
                        task_id = str(item.get("question_id", ""))
                        snippet_id = str(item.get("program_id", ""))
                        
                        if "code_gpt_score" in item and "code_gpt_score" in item["code_gpt_score"]:
                            score = item["code_gpt_score"]["code_gpt_score"]
                        else:
                            score = None
                        
                        row_key = (paper_model, lang, task_id, snippet_id, s)
                        
                        if row_key not in master_data:
                            missing_logs.append(f"[ORPHAN_LLM_SCORE] GT 데이터에 없는 스니펫/샘플 발견: Lang={lang}, Task={task_id}, Snippet={snippet_id}, Sample={s}")
                            
                        if paper_method in master_data[row_key]:
                            duplicate_logs.append(f"[SCORE_OVERWRITE] 이미 존재하는 점수 덮어쓰기: Key={row_key}, Method={paper_method}")
                            
                        master_data[row_key][paper_method] = score

    # ==========================================
    # 3. 데이터프레임 변환 및 누락 검사
    # ==========================================
    print("--- 3. 데이터프레임 변환 및 전처리 ---")
    rows = []
    for (paper_model, lang, task_id, snippet_id, s), metrics in master_data.items():
        row = {
            "LLM_MODEL": paper_model,
            "LANG": lang,
            "TASK_ID": task_id,
            "SNIPPET_ID": snippet_id,
            "SAMPLE_ID": s
        }
        row.update(metrics)
        rows.append(row)
        
    df = pd.DataFrame(rows)
    
    for col in columns_order:
        if col not in df.columns:
            df[col] = pd.NA

    df = df[columns_order]

    nan_counts = df.isna().sum()
    if nan_counts.sum() > 0:
        print("\n[알림] 데이터 중 누락된 값(NaN)이 존재합니다:")
        print(nan_counts[nan_counts > 0])
        cols_to_check = ["EXECUTION_PLUS_BINARY", "EXECUTION_PLUS_RATIO", "V-Score", "V-Score w/o r"]
        missing_details_df = df[df[cols_to_check].isna().any(axis=1)]
        
        # 확인하기 편하도록 식별자(ID)에 SAMPLE_ID를 추가하여 저장
        inspect_columns = ["LLM_MODEL", "LANG", "TASK_ID", "SNIPPET_ID", "SAMPLE_ID"] + cols_to_check
        missing_csv_path = os.path.join(HERE, "missing_data_details.csv")
        
        missing_details_df[inspect_columns].to_csv(missing_csv_path, index=False, encoding='utf-8')
        print(f" -> [추적 완료] 누락이 발생한 상세 위치(ID)를 다음 파일에 저장했습니다: {missing_csv_path}")

    # ==========================================
    # 4. 파일 저장 및 로그 출력
    # ==========================================
    output_csv = os.path.join(HERE, "HumanEval_X_Plus_Total_Results.csv")
    output_parquet = os.path.join(HERE, "HumanEval_X_Plus_Total_Results.parquet")
    
    df.to_csv(output_csv, index=False, encoding='utf-8')
    df.to_parquet(output_parquet, index=False)
    
    print(f"\n[완료] 마스터 데이터 저장 성공")
    print(f" - CSV: {output_csv}")
    print(f" - Parquet: {output_parquet}")
    print(f" - 총 행(Row) 수: {len(df)}")

    print("\n[완료] 모델별 분할 데이터 저장")
    for model_name in df["LLM_MODEL"].unique():
        if pd.isna(model_name): continue 
            
        model_df = df[df["LLM_MODEL"] == model_name]
        safe_model_name = str(model_name).replace(".", "_").replace(" ", "_")
        
        m_csv = os.path.join(HERE, f"HumanEval_X_Plus_{safe_model_name}.csv")
        m_parquet = os.path.join(HERE, f"HumanEval_X_Plus_{safe_model_name}.parquet")
        
        model_df.to_csv(m_csv, index=False, encoding='utf-8')
        model_df.to_parquet(m_parquet, index=False)
        print(f" - {model_name}: {len(model_df)} rows -> {m_csv}")

    log_file = os.path.join(HERE, "merge_logs.txt")
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("=== Missing Data Logs ===\n")
        f.write("\n".join(missing_logs) if missing_logs else "No missing data.\n")
        f.write("\n\n=== Duplicate Logs ===\n")
        f.write("\n".join(duplicate_logs) if duplicate_logs else "No duplicates found.\n")
        
    print(f"\n - 상세 로그 파일: {log_file}")


if __name__ == "__main__":
    main()