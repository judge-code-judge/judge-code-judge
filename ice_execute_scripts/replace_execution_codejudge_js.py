import os
import json
import glob
import random
import subprocess
import multiprocessing as mp
import threading
import queue
import itertools

from evalplus.data import get_human_eval_plus


# =========================================================
# config
# =========================================================

TIMEOUT = 5

HUMANEVAL_PATH = "../human-eval-v2-20210705.jsonl"

RUNNER_JS_PATH = "../runner.js"

JS_SMALL_BASE = (
    "../codejudge_scripts/humaneval/output/humaneval/js-small-test"
)

JS_VALIDATION_BASE = (
    "../codejudge_scripts/humaneval/output/humaneval/js-small-validation"
)

NUM_WORKERS = 16


# =========================================================
# utils
# =========================================================

def load_humaneval(path):
    tasks = {}

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            task = json.loads(line)
            tasks[task["task_id"]] = task

    return tasks


def load_question_program_dict(base_path):
    """
    Load one random json file.

    Returns:
        {
            qid: {
                "program": ...,
                "canonical_solution": ...
            }
        }
    """

    json_paths = glob.glob(
        f"{base_path}/**/*.json",
        recursive=True
    )

    if not json_paths:
        raise ValueError(f"No json files in {base_path}")

    selected_path = random.choice(json_paths)

    print(f"[INFO] using: {selected_path}")

    with open(selected_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    result = {}

    for item in data["data"]:
        qid = item["question_id"]

        result[qid] = {
            "program": item["program"],
            "canonical_solution": item.get(
                "canonical_solution",
                ""
            )
        }

    return result


# =========================================================
# persistent node runner
# =========================================================

class PersistentNodeRunner:

    def __init__(self, runner_path):

        self.proc = subprocess.Popen(
            ["node", runner_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

        self.lock = threading.Lock()

    def run(self, code, func_name, args):

        req = {
            "code": code,
            "func_name": func_name,
            "args": args,
        }

        try:

            with self.lock:

                self.proc.stdin.write(
                    json.dumps(req) + "\n"
                )

                self.proc.stdin.flush()

                line = self.proc.stdout.readline()

            if not line:
                return "ExecutionError"

            res = json.loads(line)

            if res.get("ok"):
                return res["result"]

            else:
                return res.get(
                    "error",
                    "ExecutionError"
                )

        except Exception as e:
            return f"ExecutionError: {str(e)}"

    def close(self):

        try:
            self.proc.kill()
        except:
            pass


# =========================================================
# global worker state
# =========================================================

_worker_runner = None
_worker_problems = None


def init_worker():

    global _worker_runner
    global _worker_problems

    _worker_runner = PersistentNodeRunner(
        RUNNER_JS_PATH
    )

    _worker_problems = get_human_eval_plus()

    print(
        f"[Worker {os.getpid()}] node started"
    )


# =========================================================
# evaluation
# =========================================================

HUMANEVAL_TASKS = load_humaneval(
    HUMANEVAL_PATH
)


def evaluate_single_program(args):

    global _worker_runner
    global _worker_problems

    qid, obj = args

    task_id = f"HumanEval/{qid}"

    if task_id not in _worker_problems:
        return qid, 0.0

    problem = _worker_problems[task_id]

    entry_point = problem["entry_point"]

    inputs = (
        problem["base_input"]
        # + problem["plus_input"]
    )

    candidate_program = obj["program"]

    canonical_solution = obj["canonical_solution"]

    # -------------------------------------------------
    # precompute gold outputs once
    # -------------------------------------------------

    gold_outputs = []

    for inp in inputs:

        gold = _worker_runner.run(
            canonical_solution,
            entry_point,
            inp
        )

        gold_outputs.append(gold)

    # -------------------------------------------------
    # evaluate candidate
    # -------------------------------------------------

    passed = 0

    for inp, gold in zip(inputs, gold_outputs):

        pred = _worker_runner.run(
            candidate_program,
            entry_point,
            inp
        )

        if pred == gold:
            passed += 1

    ratio = passed / len(inputs)

    print(f"{qid}: {ratio}")

    return qid, ratio


# =========================================================
# json utils
# =========================================================

def atomic_save_json(data, path):
    """
    Atomic json save.
    Prevents corruption on interruption.
    """

    tmp_path = path + ".tmp"

    with open(
        tmp_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            indent=4,
            ensure_ascii=False
        )

    os.replace(tmp_path, path)


# =========================================================
# main
# =========================================================

def main():

    small_tasks = load_question_program_dict(
        JS_SMALL_BASE
    )

    validation_tasks = load_question_program_dict(
        JS_VALIDATION_BASE
    )

    target_tasks = {}

    target_tasks.update(small_tasks)
    target_tasks.update(validation_tasks)

    print(
        f"[INFO] total tasks: {len(target_tasks)}"
    )

    items = list(target_tasks.items())

    # -------------------------------------------------
    # output path
    # -------------------------------------------------

    output_path = (
        "../data/humaneval/humaneval_js_base_ratio.json"
    )

    # -------------------------------------------------
    # resume support
    # -------------------------------------------------

    if os.path.exists(output_path):

        with open(
            output_path,
            "r",
            encoding="utf-8"
        ) as f:

            results = json.load(f)

        print(
            f"[INFO] loaded existing results: "
            f"{len(results)}"
        )

    else:
        results = {}

    # -------------------------------------------------
    # skip completed tasks
    # -------------------------------------------------

    items = [
        item
        for item in items
        if item[0] not in results
    ]

    print(
        f"[INFO] remaining tasks: {len(items)}"
    )

    # -------------------------------------------------
    # multiprocessing pool
    # -------------------------------------------------

    with mp.Pool(
        NUM_WORKERS,
        initializer=init_worker
    ) as pool:

        for qid, ratio in pool.imap_unordered(
            evaluate_single_program,
            items
        ):

            # update in-memory results
            results[qid] = ratio

            # immediately save
            atomic_save_json(
                results,
                output_path
            )

            print(
                f"[INFO] saved {qid}: {ratio}"
            )

    print(
        f"[INFO] final saved: {output_path}"
    )

# =========================================================
# entry
# =========================================================

if __name__ == "__main__":

    mp.set_start_method(
        "spawn",
        force=True
    )

    main()