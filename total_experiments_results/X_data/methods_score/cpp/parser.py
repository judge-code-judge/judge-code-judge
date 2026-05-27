import json
import csv
import os

def parse_json_to_csv(input_json_path, output_csv_path="output_results.csv"):
    # 1. JSON 파일 읽기
    with open(input_json_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    
    # 2. CSV 파일 생성 및 헤더 작성
    with open(output_csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["TASK_ID", "EXECUTION_BINARY"])  # CSV 컬럼 이름
        
        # 3. "data" 리스트 내부를 순회하며 데이터 기록
        for item in payload.get("data", []):
            qid = item.get("question_id")
            is_pass = item.get("pass")
            
            if qid is not None and is_pass is not None:
                # pass(True/False)를 숫자로 바꾸어 기록
                binary_score = 1.0 if is_pass else 0.0
                writer.writerow([str(qid), binary_score])

    print(f"[성공] 파싱 완료! 저장 경로: {os.path.abspath(output_csv_path)}")

# --- 사용 예시 ---
if __name__ == "__main__":
    # 변환하고 싶은 파일의 경로를 여기에 넣고 실행하시면 됩니다.
    target_file = "gpt-3.5-turbo-1106-1-0-0.0-sample-0.json"
    
    if os.path.exists(target_file):
        parse_json_to_csv(target_file, "result.csv")
    else:
        print(f"[오류] 파일을 찾을 수 없습니다: {target_file}")