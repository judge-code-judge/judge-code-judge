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


def _gold_worker(code: str, func_name, inp, queue):
    """
    prompt + canonical_solution을 합쳐서
    완전한 함수로 만든 뒤 실행
    """
    try:
        full_code = code
        # print(code)

        # -----------------------------------------
        # 2. 실행
        # -----------------------------------------
        local_env = {}
        exec(full_code, local_env)

        gold_func = local_env[func_name]
        result = gold_func(*inp)

        queue.put(("ok", result))

    except Exception as e:
        queue.put(("error", str(e)))


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


def _timeout_handler(signum, frame):
    raise TimeoutException

import signal
import contextlib
import io

def run_with_timeout(code, func_name, inp, timeout=2):
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

    except Exception as e:
        # return "ExecutionError"
        return str(e)

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




def safe_repr(obj, max_len=200):
    try:
        s = repr(obj)
        if len(s) > max_len:
            return s[:max_len] + "...<truncated>"
        return s
    except Exception:
        return "<unrepresentable>"

def evaluate_snippet(task, evalplus_task, snippet_code, func_name, key, log_path):
    task_id = int(task["task_id"].replace("HumanEval/", ""))
    task_num = task_id
    output_dir = "results/0301"


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
    # if "plus_input" in evalplus_task:
    #     all_inputs.extend(evalplus_task["plus_input"])

    total = len(all_inputs)

    if total == 0:
        return 0.0

    passed = 0

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

            if pred == gold_output or 'maximum recursion depth exceeded in comparison' in pred:
                passed += 1
            else:
                with open(log_path, "a") as f:
                    f.write(
                        f"code={task_id}_{key}\n"
                        f"input={safe_repr(inp)}, "
                        f"result={safe_repr(pred)}\n" 
                        f"expected={safe_repr(gold_output)}\n"                            
                    )


        except Exception as e:
            # print(e)
            continue

    rate = passed / total
    print(f"Pass Rate: {rate}")
    return rate

HUMANEVAL_PATH = "human-eval-v2-20210705.jsonl"

def load_humaneval(path: str) -> Dict[str, dict]:
    tasks = {}
    with open(path, "r") as f:
        for line in f:
            task = json.loads(line)
            tasks[task["task_id"]] = task
    return tasks



def evaluate_single_task(task):
    # if task['task_id'] != '99':
    #     return None

    # ⚠ worker 안에서 로드 (pickle 안정성)
    evalplus_tasks = get_human_eval_plus()
    humaneval_tasks = load_humaneval(HUMANEVAL_PATH)

    task_id = f"HumanEval/{task['task_id']}"
    if task_id not in evalplus_tasks:
        return task

    log_dir = f"results/0301/{task['task_id']}"
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, "failure_evalplus.log")

    # 기존 로그 초기화 (선택)
    with open(log_path, "w") as f:
        pass
    

    evalplus_task = evalplus_tasks[task_id]
    humaneval_task = humaneval_tasks[task_id]

    func_name = humaneval_task["entry_point"]
    print(func_name)

    for key in list(task.keys()):
        if key.startswith("grade-"):
            continue
        if key.isdigit():

            snippet_code = build_full_code(
                humaneval_task,
                task[key]
            )

            ratio = evaluate_snippet(
                humaneval_task,
                evalplus_task,
                snippet_code,
                func_name,
                key,
                log_path
            )

            grade_key = f"grade-{key}"
            if grade_key not in task:
                task[grade_key] = {}

            task[grade_key]["execution_basic"] = ratio

    return task



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




def load_and_convert_evalplus_inputs(tasknum: str, output_dir: str) -> Dict[str, List[List[Any]]]:
    """
    Read requirement-based test inputs from:
        ../results/0301/{tasknum}/inputs.json

    Convert each function-call-style string into EvalPlus input format.

    Returns:
        Dict[str, List[List[Any]]]
            {
                "requirement_1": [[[arg1]], [[arg2]], ...],
                "requirement_2": [[[arg1]], [[arg2]], ...],
                ...
            }
    """

    path = os.path.join(output_dir, str(tasknum), "inputs.json")

    with open(path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    converted = {}

    for requirement_id, call_list in raw_data.items():
        evalplus_inputs = []

        for call_str in call_list:

            # 1️⃣ 최소한의 괄호 검증 (빠른 필터)
            if call_str.count("(") != call_str.count(")"):
                continue
            if call_str.count("[") != call_str.count("]"):
                continue

            try:
                tree = ast.parse(call_str, mode="eval")
                call_node = tree.body

                if not isinstance(call_node, ast.Call):
                    continue

                args = [_safe_eval(arg) for arg in call_node.args]

                evalplus_inputs.append([args])

            except Exception:
                # ❗ 파싱 실패 input은 그냥 제거
                continue

        converted[requirement_id] = evalplus_inputs

    return converted

############################################
# Parallel main
############################################

def main():
    input_path = "data/humaneval/humaneval_python_grade.json"
    output_path = "data/humaneval/humaneval_python_grade_evalplus_ratio_basic.json"

    data = load_json(input_path)

    num_workers = 16


    with mp.Pool(processes=num_workers) as pool:

        for idx, updated_task in enumerate(
            pool.imap_unordered(evaluate_single_task, data)
        ):
            # 결과 반영
            for i in range(len(data)):
                if updated_task is None:
                    break
                if data[i]["task_id"] == updated_task["task_id"]:
                    data[i] = updated_task
                    break

            # 🔵 task 완료 즉시 저장
            atomic_save_json(data, output_path)

    print("All tasks finished.")


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()

