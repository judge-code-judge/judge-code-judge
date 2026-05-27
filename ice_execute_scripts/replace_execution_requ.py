import os
import json
from types import FunctionType
from typing import Dict, List, Any
from evalplus.data import get_human_eval_plus
import multiprocessing as mp
import ast
import operator


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


# def run_with_timeout(code, func_name, inp, timeout=2):
#     queue = mp.Queue()

#     process = mp.Process(
#         target=_gold_worker,
#         args=(code, func_name, inp, queue)
#     )

#     process.start()
#     process.join(timeout)

#     if process.is_alive():
#         process.terminate()
#         process.join()
#         return "TimeoutError"

#     if not queue.empty():
#         status, value = queue.get()
#         if status == "ok":
#             return value
#         else:
#             return f"ExecutionError: {value}"

#     return "UnknownError"


# def run_with_timeout(code, func_name, inp, timeout=2):
#     queue = mp.Queue()

#     process = mp.Process(
#         target=_gold_worker,
#         args=(code, func_name, inp, queue)
#     )

#     process.start()
#     process.join(timeout)

#     if process.is_alive():
#         process.terminate()
#         process.join()
#         return "TimeoutError"

#     try:
#         status, value = queue.get_nowait()
#     except Exception:
#         return "UnknownError"

#     if status == "ok":
#         return value
#     else:
#         return f"ExecutionError: {value}"


import signal

class TimeoutException(Exception):
    pass


def _timeout_handler(signum, frame):
    raise TimeoutException


# def run_with_timeout(code, func_name, inp, timeout=2):
#     local_env = {}
#     # exec(code, {}, local_env)
#     # func = local_env[func_name]

#     signal.signal(signal.SIGALRM, _timeout_handler)
#     signal.alarm(timeout)

#     try:
#         full_code = code
#         # print(code)

#         # -----------------------------------------
#         # 2. 실행
#         # -----------------------------------------
#         local_env = {}
#         exec(full_code, local_env)

#         gold_func = local_env[func_name]
#         result = gold_func(*inp)

#         signal.alarm(0)
#         return result

#     except TimeoutException:
#         return "TimeoutError"
#     except Exception as e:
#         # pass
#         return f"ExecutionError"


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

    # except TimeoutException:
    #     return "TimeoutError"

    except Exception as e:
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

    print(all_inputs)

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

HUMANEVAL_PATH = "human-eval-v2-20210705.jsonl"

def load_humaneval(path: str) -> Dict[str, dict]:
    tasks = {}
    with open(path, "r") as f:
        for line in f:
            task = json.loads(line)
            tasks[task["task_id"]] = task
    return tasks

############################################
# Worker
############################################

def evaluate_single_task(task):
    # if task['task_id'] != '99':
    #     return None

    from evalplus.data import get_human_eval_plus

    # ⚠ worker 안에서 로드 (pickle 안정성)
    evalplus_tasks = get_human_eval_plus()
    humaneval_tasks = load_humaneval(HUMANEVAL_PATH)

    task_id = f"HumanEval/{task['task_id']}"
    if task_id not in evalplus_tasks:
        return task
    

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
                func_name
            )

            grade_key = f"grade-{key}"
            if grade_key not in task:
                task[grade_key] = {}

            task[grade_key]["execution"] = ratio

    return task


def safe_repr(obj, max_len=200):
    try:
        s = repr(obj)
        if len(s) > max_len:
            return s[:max_len] + "...<truncated>"
        return s
    except Exception:
        return "<unrepresentable>"


def evaluate_single_task_custom(args):
    task, requ_input_dict = args   # ← 여기서 unpack

    task_id = int(task["task_id"])
    task_num = task_id
    output_dir = "results/0301"

    # -----------------------------------
    # 1. testset 로드
    # -----------------------------------
    requ_input_dict = load_and_convert_evalplus_inputs(
        task_id,
        output_dir
    )
    # ⚠ worker 안에서 로드 (pickle 안정성)
    # evalplus_tasks = get_human_eval_plus()
    humaneval_tasks = load_humaneval(HUMANEVAL_PATH)

    task_id_he = f"HumanEval/{task['task_id']}"
    humaneval_task = humaneval_tasks[task_id_he]
    func_name = humaneval_task["entry_point"]

    # -----------------------------------
    # 2. 로그 파일 준비
    # -----------------------------------
    log_dir = f"results/0301/{task_num}"
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, "failure.log")

    # 기존 로그 초기화 (선택)
    with open(log_path, "w") as f:
        pass

    # -----------------------------------
    # 3. snippet 평가
    # -----------------------------------
    for key in list(task.keys()):
        if key.startswith("grade-"):
            continue
        if not key.isdigit():
            continue

        snippet_code = build_full_code(humaneval_task, task[key])
        # print(f"{task_id}_{key}")
        sol_code = build_solution(humaneval_task)

        total = 0
        passed = 0
        
        for requirement, testcases in requ_input_dict.items():
            
            for case in testcases:
                # print(case)
                inp = case[0]

                expected = run_with_timeout(sol_code, func_name, inp, TIMEOUT)
                result = run_with_timeout(
                    snippet_code,
                    func_name,
                    inp, 
                    TIMEOUT
                )

                total += 1

                if result == expected:
                    passed += 1
                else:
                    # 🔴 실패 로그 기록
                    # print(case[0])
                    with open(log_path, "a") as f:
                        f.write(
                            f"code={task_id}_{key}\n"
                            f"input={safe_repr(case[0])}, "
                            f"result={safe_repr(result)}\n" 
                            f"expected={safe_repr(expected)}\n"                            
                        )

        ratio = passed / total if total > 0 else 0.0

        print(f"Pass Ratio: {ratio}")

        grade_key = f"grade-{key}"
        if grade_key not in task:
            task[grade_key] = {}

        task[grade_key]["execution_custom"] = ratio

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
    output_path = "data/humaneval/humaneval_python_grade_ratio_requ.json"

    data = load_json(input_path)

    num_workers = 16

    output_dir = "results/0301"

    tasks_for_pool = []

    for task in data:
        task_id = int(task["task_id"])   # 예: 0 ~ 163

        requ_input_dict = load_and_convert_evalplus_inputs(
            task_id,
            output_dir
        )

        tasks_for_pool.append((task, requ_input_dict))

    with mp.Pool(processes=num_workers) as pool:

        for idx, updated_task in enumerate(
            pool.imap_unordered(evaluate_single_task_custom, tasks_for_pool)
        ):

            if updated_task is None:
                continue

            # 결과 반영
            for i in range(len(data)):
                if data[i]["task_id"] == updated_task["task_id"]:
                    data[i] = updated_task
                    break

            # 🔵 task 완료 즉시 저장
            atomic_save_json(data, output_path)

            # print(f"[{idx+1}/{len(data)}] saved HumanEval/{updated_task['task_id']}")

    print("All tasks finished.")


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()