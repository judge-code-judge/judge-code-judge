import json
import subprocess
import tempfile
import os
from evalplus.data import get_human_eval_plus
from tqdm import tqdm
from typing import Dict, List, Any

JS_FILE = "data/humaneval-x/js.jsonl"
GRADE_FILE = "data/humaneval-x/humaneval_js_grade.json"
OUTPUT_FILE = "data/humaneval-x/humaneval_js_grade_with_execution_basic.json"

import re
import select

def extract_func_name(declaration):
    match = re.search(r'const\s+(\w+)\s*=', declaration)
    if not match:
        raise ValueError(f"Cannot parse function name from: {declaration}")
    return match.group(1)


class NodeRunner:
    def __init__(self, script_path="runner.js"):
        self.script_path = script_path
        self.counter = 0   # 🔥 여기서 초기화

        self.proc = subprocess.Popen(
            ["node", script_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

    def restart(self):
        try:
            self.proc.kill()
        except:
            pass

        self.proc = subprocess.Popen(
            ["node", self.script_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

    def run(self, code, func_name, args, timeout=2):
        self.counter += 1   # 🔥 (1) 여기!

        # 🔥 (2) 일정 횟수마다 재시작
        if self.counter % 200 == 0:
            # print("[INFO] Restarting Node (periodic)")
            self.restart()

        req = {
            "code": code,
            "func_name": func_name,
            "args": args,
        }

        try:
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()

            ready, _, _ = select.select([self.proc.stdout], [], [], timeout)

            if not ready:
                print("[TIMEOUT] Restarting Node...")
                self.restart()
                return {"ok": False, "error": "[TIMEOUT]"}

            line = self.proc.stdout.readline()

            if not line:
                # print("[ERROR] Node died → restarting")
                self.restart()
                return {"ok": False, "error": "Node died"}

            return json.loads(line)

        except Exception as e:
            # print("[EXCEPTION] Restarting Node...", e)
            self.restart()
            return {"ok": False}

    def close(self):
        self.proc.kill()

# -----------------------------
# Load JS tasks
# -----------------------------
def load_js_tasks(path):
    tasks = {}
    with open(path, "r") as f:
        for line in f:
            data = json.loads(line)
            tid = data["task_id"].split("/")[-1]
            tasks[tid] = data
    return tasks


def parse_output(output):
    try:
        data = json.loads(output)
        return data
    except:
        return None


# -----------------------------
# canonical solution 구성
# -----------------------------
def build_canonical_code(task):
    return task["declaration"] + task["canonical_solution"]


# -----------------------------
# generated solution 구성
# -----------------------------
def build_generated_code(task, snippet):
    return task["declaration"] + snippet


HUMANEVAL_PATH = "human-eval-v2-20210705.jsonl"

def load_humaneval(path: str) -> Dict[str, dict]:
    tasks = {}
    with open(path, "r") as f:
        for line in f:
            task = json.loads(line)
            tasks[task["task_id"]] = task
    return tasks


def evaluate_single_task_js(item):
    global RUNNER, JS_TASKS, EVALPLUS_TASKS

    js_tasks = JS_TASKS
    evalplus_tasks = EVALPLUS_TASKS

    task_id = item["task_id"]

    if task_id not in js_tasks:
        return item

    evalplus_id = f"HumanEval/{task_id}"
    if evalplus_id not in evalplus_tasks:
        return item

    task = js_tasks[task_id]
    evalplus_task = evalplus_tasks[evalplus_id]

    func_name = extract_func_name(task["declaration"])

    log_dir = f"results/js_eval/{task_id}"
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "failures.log")

    open(log_path, "w").close()

    for key in list(item.keys()):
        if not key.isdigit():
            continue

        snippet = item[key]
        snippet_code = build_generated_code(task, snippet)

        ratio = evaluate_snippet_js(
            task,
            evalplus_task,
            snippet_code,
            func_name,
            key,
            log_path,
            RUNNER,
        )
        print(f"Pass Ratio: {ratio}")

        grade_key = f"grade-{key}"
        if grade_key not in item:
            item[grade_key] = {}

        item[grade_key]["execution_custom"] = ratio

    return item


def evaluate_snippet_js(task, evalplus_task, snippet_code, func_name, key, log_path, runner):
    canonical_code = build_canonical_code(task)

    all_inputs = []
    if "base_input" in evalplus_task:
        all_inputs.extend(evalplus_task["base_input"])
    if "plus_input" in evalplus_task:
        all_inputs.extend(evalplus_task["plus_input"])

    total = len(all_inputs)
    if total == 0:
        return 0.0

    passed = 0

    first_fail = True

    for inp in all_inputs:
        try:
            gold = runner.run(canonical_code, func_name, inp)
            pred = runner.run(snippet_code, func_name, inp)

            # print(gold)

            if not gold.get("ok"):
                continue
            if not pred.get("ok"):
                if first_fail == True:
                    with open(log_path, "a") as f:
                        f.write(f"[PRED ERROR]\ninput={inp}\n{pred}\n\n")
                    first_fail = False
                continue

            if pred.get("ok") and pred["result"] == gold["result"]:
                passed += 1
            else:
                # print("Wrong")
                # print(f"expected={gold}, got={pred}")
                if first_fail == True:
                    with open(log_path, "a") as f:
                        f.write(
                            f"code={task['task_id']}_{key}\n"
                            f"input={inp}\n"
                            f"expected={gold}, got={pred}\n\n"
                        )
                        print(log_path)
                    first_fail = False

        except Exception as e:
            if first_fail == True:
                with open(log_path, "a") as f:
                    f.write(
                        f"code={task['task_id']}_{key}\n"
                        f"input={inp}, {str(e)}\n"
                    )
                    print(log_path)
                first_fail = False

            continue

    return passed / total

def atomic_save_json(data, path):
    import tempfile, os, json

    dir_name = os.path.dirname(path)

    with tempfile.NamedTemporaryFile(
        mode="w", dir=dir_name, delete=False, suffix=".tmp"
    ) as tmp:
        json.dump(data, tmp, indent=2)
        temp_name = tmp.name

    os.replace(temp_name, path)

# -----------------------------
# 메인 평가 루프
# -----------------------------
import multiprocessing as mp
from tqdm import tqdm
import json

RUNNER = None
JS_TASKS = None
EVALPLUS_TASKS = None


def init_worker():
    global RUNNER
    RUNNER = NodeRunner("runner.js")
    global JS_TASKS, EVALPLUS_TASKS
    JS_TASKS = load_js_tasks(JS_FILE)
    EVALPLUS_TASKS = get_human_eval_plus()


def main():
    with open(GRADE_FILE, "r") as f:
        data = json.load(f)

    num_workers = min(16, mp.cpu_count())

    with mp.Pool(
    processes=num_workers,
    initializer=init_worker
    ) as pool:
        for updated_task in tqdm(
            pool.imap_unordered(evaluate_single_task_js, data),
            total=len(data),
        ):
            if updated_task is None:
                continue

            # 결과 반영
            for i in range(len(data)):
                if data[i]["task_id"] == updated_task["task_id"]:
                    data[i] = updated_task
                    break

            # 🔥 task 끝날 때마다 저장
            atomic_save_json(data, OUTPUT_FILE)

    print(f"Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()