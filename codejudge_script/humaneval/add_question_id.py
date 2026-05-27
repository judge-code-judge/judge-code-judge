import json
import glob
import os


def add_question_id_to_other_metrics(
    base_dir="./output/humaneval/python-small-test",
    reference_file="CodeLlama-34b-Instruct-1-0-0.4-sample-0.json",
):

    # 1️⃣ reference file 읽기
    ref_path = os.path.join(base_dir, reference_file)

    with open(ref_path, "r") as f:
        ref_data = json.load(f)

    # question_id 목록 추출 (순서 유지)
    question_ids = [item["question_id"] for item in ref_data["data"]]

    # 2️⃣ other-metrics 파일 수집
    pattern = os.path.join(base_dir, "other-metrics-without-prefix-sample-*.json")
    metric_files = glob.glob(pattern)

    print(f"Found {len(metric_files)} other-metrics files")

    # 3️⃣ 각 파일에 question_id 추가
    for path in metric_files:

        with open(path, "r") as f:
            data = json.load(f)

        if len(data) != len(question_ids):
            raise ValueError(
                f"Length mismatch: {path} ({len(data)}) vs reference ({len(question_ids)})"
            )

        # question_id 삽입
        for i, item in enumerate(data):
            item["question_id"] = question_ids[i]

        # 저장 (overwrite)
        with open(path, "w") as f:
            json.dump(data, f, indent=4)

        print("updated:", path)

add_question_id_to_other_metrics()