import os
import json
import glob
import pandas as pd
from collections import defaultdict

# ==========================================
# 환경 변수 및 디렉토리 설정
# ==========================================
HERE = os.path.dirname(os.path.abspath(__file__))
EXEC_DIR = os.path.join(HERE, "execution_score")
LLM_DIR = os.path.join(HERE, "llm_evaluate_score")

benchmarks = ["apps", "mbpp"]

# 1. Method 및 Metric 매핑 딕셔너리
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

# GPT-5-nano 모델만 실험에 사용됨을 반영
llm_model_mapping = {
    "gpt-5-nano": "GPT-5-nano",
}

# 2. 최종 출력 컬럼 순서 정의
columns_order = [
    "BENCHMARK", "LLM_MODEL", "TASK_ID", "SNIPPET_ID", "EXECUTION_BINARY", "EXECUTION_RATIO",
    "ICE-Score", "ICE-Score w/o r", "CJ-BIN", "CJ-BIN w/o r", "CJ-Score", "CJ-Score w/o r",
    "V-BIN", "V-BIN w/o r", "V-Score", "V-Score w/o r"
]

def main():
    all_rows = []
    missing_logs = []
    duplicate_logs = []

    for benchmark in benchmarks:
        print(f"🔄 [{benchmark.upper()}] 데이터 로드 및 파싱 중...")
        master_data = defaultdict(dict)
        gt_cache = {}
        
        # ------------------------------------------
        # 1. Execution Data 파싱 (Ground Truth)
        # ------------------------------------------
        exec_file_path = os.path.join(EXEC_DIR, f"{benchmark}_pass_ratio_results.json")
        if not os.path.exists(exec_file_path):
            missing_logs.append(f"[{benchmark.upper()}_GT_MISSING] 파일 없음: {exec_file_path}")
            continue
            
        with open(exec_file_path, "r", encoding="utf-8") as f:
            try:
                data_list = json.load(f)
            except json.JSONDecodeError:
                missing_logs.append(f"[{benchmark.upper()}_GT_ERROR] JSON 파싱 에러: {exec_file_path}")
                continue

        for item in data_list:
            task_id = str(item.get("task_id", ""))
            solutions = item.get("solutions", None)
            
            if not task_id or not solutions:
                continue
                
            if benchmark == "apps":
                for sol in solutions:
                    snippet_id = str(sol.get("data_id", ""))
                    ratio = float(sol.get("ratio", 0.0))
                    binary = 1 if ratio == 1.0 else 0
                    gt_cache[(task_id, snippet_id)] = {
                        "EXECUTION_RATIO": ratio,
                        "EXECUTION_BINARY": binary
                    }
                    
            elif benchmark == "mbpp":
                for key, sol in solutions.items():
                    snippet_id = str(key)
                    ratio = float(sol.get("ratio", 0.0))
                    binary = 1 if ratio == 1.0 else 0
                    gt_cache[(task_id, snippet_id)] = {
                        "EXECUTION_RATIO": ratio,
                        "EXECUTION_BINARY": binary
                    }

        # Ground Truth 기반으로 Base 구조 생성 (GPT-5-nano 단일 모델)
        for (task_id, snippet_id), gt_record in gt_cache.items():
            for raw_model, paper_model in llm_model_mapping.items():
                row_key = (paper_model, task_id, snippet_id)
                master_data[row_key].update(gt_record)

        # ------------------------------------------
        # 2. LLM-Based Methods Score 파싱
        # ------------------------------------------
        benchmark_llm_dir = os.path.join(LLM_DIR, benchmark)
        
        for raw_model, paper_model in llm_model_mapping.items():
            for raw_method, paper_method in llm_method_map.items():
                search_pattern = os.path.join(benchmark_llm_dir, f"{raw_model}-{raw_method}-*_{benchmark}*.json")
                matched_files = glob.glob(search_pattern)
                
                if not matched_files:
                    missing_logs.append(f"[{benchmark.upper()}_LLM_MISSING] 파일 없음: {search_pattern}")
                    continue
                
                if len(matched_files) > 1:
                    duplicate_logs.append(f"[{benchmark.upper()}_LLM_DUPLICATE] 다중 매칭: {search_pattern}. 첫 번째 파일 사용.")
                
                file_path = matched_files[0]
                with open(file_path, "r", encoding="utf-8") as f:
                    try:
                        llm_json = json.load(f)
                    except json.JSONDecodeError:
                        missing_logs.append(f"[{benchmark.upper()}_LLM_ERROR] 파싱 에러: {file_path}")
                        continue
                        
                for item in llm_json.get("data", []):
                    task_id = str(item.get("question_id", ""))
                    snippet_id = str(item.get("program_id", ""))
                    
                    if "code_gpt_score" in item and "code_gpt_score" in item["code_gpt_score"]:
                        score = item["code_gpt_score"]["code_gpt_score"]
                    else:
                        score = None
                    
                    row_key = (paper_model, task_id, snippet_id)
                    
                    if row_key not in master_data:
                        missing_logs.append(f"[{benchmark.upper()}_ORPHAN_LLM] GT에 없는 스니펫: Task={task_id}, Snippet={snippet_id}")
                        continue
                        
                    if paper_method in master_data[row_key]:
                        duplicate_logs.append(f"[{benchmark.upper()}_SCORE_OVERWRITE] 덮어쓰기: Key={row_key}, Method={paper_method}")
                        
                    master_data[row_key][paper_method] = score

        # 마스터 데이터를 딕셔너리에서 리스트 로우 형태로 변환하여 누적
        for (paper_model, task_id, snippet_id), metrics in master_data.items():
            row = {
                "BENCHMARK": benchmark.upper(),
                "LLM_MODEL": paper_model,
                "TASK_ID": task_id,
                "SNIPPET_ID": snippet_id
            }
            row.update(metrics)
            all_rows.append(row)

    # ------------------------------------------
    # 3. 단일 데이터프레임 병합 및 전처리
    # ------------------------------------------
    if not all_rows:
        print("❌ 파싱된 데이터가 없어 통합 파일을 생성하지 못했습니다.")
        return

    df = pd.DataFrame(all_rows)
    
    # 누락된 Method 컬럼들을 NaN으로 보장
    for col in columns_order:
        if col not in df.columns:
            df[col] = pd.NA

    # 컬럼 순서 고정
    df = df[columns_order]

    # ------------------------------------------
    # 4. 하나의 통합 마스터 파일로 저장
    # ------------------------------------------
    output_csv = os.path.join(HERE, "APPS_MBPP_Total_Results.csv")
    output_parquet = os.path.join(HERE, "APPS_MBPP_Total_Results.parquet")
    
    df.to_csv(output_csv, index=False, encoding='utf-8')
    df.to_parquet(output_parquet, index=False)
    
    print(f"✨ [완료] 단일 통합 마스터 데이터 저장 성공")
    print(f" - CSV: {output_csv}")
    print(f" - Parquet: {output_parquet}")
    print(f" - 총 통합 행(Row) 수: {len(df)}")

    # 결측치 세부 추적 파일 생성
    cols_to_check = ["EXECUTION_BINARY", "EXECUTION_RATIO", "V-Score", "V-Score w/o r"]
    missing_details_df = df[df[cols_to_check].isna().any(axis=1)]
    if len(missing_details_df) > 0:
        missing_csv_path = os.path.join(HERE, "apps_mbpp_missing_details.csv")
        inspect_columns = ["BENCHMARK", "LLM_MODEL", "TASK_ID", "SNIPPET_ID"] + cols_to_check
        missing_details_df[inspect_columns].to_csv(missing_csv_path, index=False, encoding='utf-8')
        print(f" -> [추적 완료] 누락 발생 상세 위치(ID) 저장: {missing_csv_path}")

    # 종합 로그 파일 기록
    log_file = os.path.join(HERE, "merge_logs.txt")
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("=== Missing Data Logs ===\n")
        f.write("\n".join(missing_logs) if missing_logs else "No missing data.\n")
        f.write("\n\n=== Duplicate Logs ===\n")
        f.write("\n".join(duplicate_logs) if duplicate_logs else "No duplicates found.\n")
    print(f" - 상세 로그 파일 저장 완료: {log_file}")

if __name__ == "__main__":
    main()