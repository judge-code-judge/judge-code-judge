import json
import os
import subprocess
import tempfile
from datasets import load_dataset
from multiprocessing import Pool, cpu_count, Manager
from collections import defaultdict
from tqdm import tqdm


APPS_TEST_PATH = "data/APPS/test"
LOG_DIR = "results/apps"

os.makedirs(LOG_DIR, exist_ok=True)


def run_code(code, input_data, timeout=2):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            temp_path = f.name

        result = subprocess.run(
            ["python", temp_path],
            input=input_data,
            text=True,
            capture_output=True,
            timeout=timeout,
        )

        return result.stdout.strip()

    except Exception:
        return None

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def compute_pass_ratio_with_logging(task_id, data_id, code, inputs, outputs, lock):
    total = len(inputs)
    passed = 0
    fail_logs = []

    for inp, expected in zip(inputs, outputs):
        out = run_code(code, inp)

        if out is None or out.strip() != expected.strip():
            fail_logs.append({
                "input": inp,
                "expected": expected,
                "got": out
            })
        else:
            passed += 1

    # 🔴 task 단위 로그 파일
    if fail_logs:
        log_path = os.path.join(LOG_DIR, f"{task_id}_fail.log")

        with lock:
            with open(log_path, "a") as f:
                f.write(f"\n===== Candidate {data_id} =====\n")
                for i, log in enumerate(fail_logs):
                    f.write(f"\n[FAIL {i}]\n")
                    f.write(f"input:\n{log['input']}\n")
                    f.write(f"expected:\n{log['expected']}\n")
                    f.write(f"got:\n{log['got']}\n")

    return passed / total if total > 0 else 0.0


def process_task(args):
    task_id, items, lock = args

    task_id_padded = str(task_id).zfill(4)
    apps_path = os.path.join(APPS_TEST_PATH, task_id_padded, "input_output.json")

    if not os.path.exists(apps_path):
        return None

    with open(apps_path, "r") as f:
        io_data = json.load(f)

    inputs = io_data["inputs"]
    outputs = io_data["outputs"]

    solutions_list = []

    for item in items:
        data_id = str(item["data_id"])
        code = item["code"]

        ratio = compute_pass_ratio_with_logging(
            str(task_id), data_id, code, inputs, outputs, lock
        )

        print(f"Pass ratio: {ratio}")

        solutions_list.append({
            "data_id": data_id,
            "solution": code,
            "ratio": ratio,
        })

    return {
        "task_id": str(task_id),
        "solutions": solutions_list
    }


def main():
    ds = load_dataset("CodeResearch/CodeJudge-Eval")["train"]
    print(ds[0].keys())

    # # 🔥 task_id 기준 groupby
    # task_groups = defaultdict(list)
    # for item in ds:
    #     # print(item["task_id"])
    #     task_groups[item["task_id"]].append(item)

    # print(task_groups.keys())

    # manager = Manager()
    # lock = manager.Lock()

    # num_workers = max(1, cpu_count() - 1)

    # tasks = [(task_id, items, lock) for task_id, items in task_groups.items()]

    # with Pool(num_workers) as pool:
    #     results = list(
    #         tqdm(pool.imap_unordered(process_task, tasks), total=len(tasks))
    #     )

    # results = [r for r in results if r is not None]

    # with open("pass_ratio_results.json", "w") as f:
    #     json.dump(results, f, indent=4)


if __name__ == "__main__":
    main()