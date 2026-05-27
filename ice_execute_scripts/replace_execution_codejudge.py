import os
import json
from types import FunctionType
from typing import Dict, List, Any
from evalplus.data import get_human_eval_plus
import multiprocessing as mp
import ast
import operator
import glob
import random


TIMEOUT = 5 # 초 단위


import textwrap


# 허용할 연산자 매핑
_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}



def build_solution(task: dict):
    # print(task.keys())
    prompt = task["prompt"]
    
    body = task["canonical_solution"]
    func_name = task["entry_point"]

    # -----------------------------------------
    # 1. prompt + body 합치기
    # -----------------------------------------
    full_code = prompt + textwrap.indent(
        textwrap.dedent(body),
        "    "
    )
    return full_code


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def atomic_save_json(data, path):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(tmp_path, path)


def extract_function_name(intent: str) -> str:
    print(intent)
    first_line = intent.strip().split("\n")[0]
    name = first_line.split("def")[1].split("(")[0].strip()
    return name



import signal

class TimeoutException(Exception):
    pass


import signal
import contextlib
import io

def run_with_timeout(code, func_name, inp, timeout=5):
    def _timeout_handler(signum, frame):
        raise TimeoutException()

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)

    try:
        full_code = code

        # -----------------------------------------
        # stdout / stderr 차단
        # -----------------------------------------
        f = io.StringIO()
        with contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):

            local_env = {}
            exec(full_code, local_env)

            gold_func = local_env[func_name]
            result = gold_func(*inp)


        signal.alarm(0)
        return result

    except TimeoutException:
        return "TimeoutError"

    except Exception:
        return "ExecutionError"

    finally:
        # 혹시 모를 alarm 잔존 방지
        signal.alarm(0)


def build_full_code(task, body):
    try:
        prompt = task["prompt"]

        # -----------------------------------------
        # 1. prompt + body 합치기
        # -----------------------------------------
        full_code = prompt + "    " + body
        # print(full_code)
    except:
        return ""
    return full_code


def evaluate_snippet(task, evalplus_task, snippet_code, func_name):
    local_env = {}

    try:
        exec(snippet_code, {}, local_env)
    except Exception:
        return 0.0

    if func_name not in local_env:
        print("func_name not in local_env")
        return 0.0

    func = local_env[func_name]
    if not isinstance(func, FunctionType):
        return 0.0

    # 전체 테스트 = base + plus
    all_inputs = []
    if "base_input" in evalplus_task:
        all_inputs.extend(evalplus_task["base_input"])
    if "plus_input" in evalplus_task:
        all_inputs.extend(evalplus_task["plus_input"])

    total = len(all_inputs)
    if total == 0:
        return 0.0

    passed = 0

    # print(all_inputs)

    for inp in all_inputs:
        try:
            # gold output 생성
            sol_code = build_solution(task)
            # print(sol_code)
            gold_output = run_with_timeout(sol_code, func_name, inp, TIMEOUT)
            pred = run_with_timeout(snippet_code, func_name, inp, TIMEOUT)

            # # model output 생성
            # if isinstance(inp, tuple) or isinstance(inp, list):
            #     pred = func(*inp)
            # else:
            #     pred = func(inp)

            # print(gold_output)
            # print(f"{gold_output}, {pred}")

            if pred == gold_output:
                passed += 1

        except Exception as e:
            # print(e)
            continue

    rate = passed / total
    print(f"Pass Rate: {rate}")
    return rate

HUMANEVAL_PATH = "../human-eval-v2-20210705.jsonl"

def load_humaneval(path: str) -> Dict[str, dict]:
    tasks = {}
    with open(path, "r") as f:
        for line in f:
            task = json.loads(line)
            tasks[task["task_id"]] = task
    return tasks



def evaluate_single_program(args):
    # print(args)
    qid, program = args

    problems = get_human_eval_plus()
    humaneval_tasks = load_humaneval(HUMANEVAL_PATH)
    task_id = f"HumanEval/{qid}"

    if task_id not in problems:
        return qid, 0

    problem = problems[task_id]

    prompt = problem["prompt"]
    entry_point = problem["entry_point"]

    # full_code = prompt + program
    full_code = program

    # inputs = problem["base_input"] + problem["plus_input"]
    inputs = problem["base_input"]

    passed = 0
    solution_code = build_solution(humaneval_tasks[task_id])

    for inp in inputs:


        result = run_with_timeout(full_code, entry_point, inp, 5)
        exp = run_with_timeout(solution_code, entry_point, inp, 5)

        if result == exp:
            passed += 1

    ratio = passed / len(inputs)

    print(f"{qid}: {ratio}")
    return qid, ratio


def _safe_eval(node):
    if isinstance(node, ast.Constant):
        return node.value

    elif isinstance(node, ast.List):
        return [_safe_eval(elt) for elt in node.elts]

    elif isinstance(node, ast.Tuple):
        return tuple(_safe_eval(elt) for elt in node.elts)

    elif isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type in _ALLOWED_OPERATORS:
            return _ALLOWED_OPERATORS[op_type](_safe_eval(node.operand))
        raise ValueError

    elif isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type in _ALLOWED_OPERATORS:
            return _ALLOWED_OPERATORS[op_type](
                _safe_eval(node.left),
                _safe_eval(node.right),
            )
        raise ValueError

    else:
        raise ValueError
    

def load_question_program_dict(base_path="evaluation/humaneval/output/humaneval/python-small-test"):
    """
    Collect json files under base_path and return
    {question_id: program} dict from one of the files.
    """

    # 1️⃣ 모든 json 파일 수집
    json_paths = glob.glob(f"{base_path}/**/*.json", recursive=True)

    if not json_paths:
        raise ValueError("No json files found.")

    # 2️⃣ 하나 선택
    selected_path = random.choice(json_paths)

    print("Using file:", selected_path)

    # 3️⃣ 파일 로드
    with open(selected_path, "r") as f:
        data = json.load(f)

    result = {}

    # 4️⃣ question_id -> program dict 생성
    for item in data.get("data", []):
        qid = item["question_id"]
        program = item["program"]
        result[qid] = program

    return result



############################################
# Parallel main
############################################

def main():
    input_path = "../data/humaneval/humaneval_python_grade.json"

    output_path = (
        "../data/humaneval/"
        "humaneval_python_codejudge_base_ratio.json"
    )

    data = load_json(input_path)

    num_workers = 16

    output_dir = "results/0301"

    target_small_tasks = load_question_program_dict(
        base_path="../data/output/humaneval/python-small-test"
    )

    target_validation_tasks = load_question_program_dict(
        base_path="../data/output/humaneval/python-small-validation"
    )

    # Python 3.9+
    target_tasks = (
        target_small_tasks
        | target_validation_tasks
    )

    print(target_tasks)

    items = list(target_tasks.items())

    results = {}

    # ----------------------------------------
    # multiprocessing evaluation
    # ----------------------------------------

    with mp.Pool(num_workers) as pool:

        for qid, ratio in pool.imap_unordered(
            evaluate_single_program,
            items
        ):
            results[qid] = ratio

    # ----------------------------------------
    # save json
    # ----------------------------------------

    with open(
        output_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            results,
            f,
            indent=4,
            ensure_ascii=False
        )

    print("All tasks finished.")
    print(f"Saved results to: {output_path}")
    

if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()