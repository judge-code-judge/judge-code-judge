import os
import json
import subprocess
import re
import shutil
import tempfile
import argparse
import multiprocessing as mp
from tqdm import tqdm
from typing import Tuple

TIMEOUT_SEC = 10
MAX_PARALLEL_TASKS = 16
GO_TASKS = None
INPUTS_MAP = None

# ==========================================
# Go Subtest Injection (for Rational Score)
# ==========================================
def inject_subtests(test_code: str) -> str:
    body_match = re.search(r'func\s+(\w+)\(t\s+\*testing\.T\)\s*\{([\s\S]*)\}', test_code)
    if not body_match:
        return test_code
    
    func_name = body_match.group(1)
    body = body_match.group(2)
    
    found_asserts = []
    start = 0
    while True:
        pos = body.find("assert.", start)
        if pos == -1: break
        if body[pos:pos+13] == "assert.New(t)":
            start = pos + 13
            continue

        paren_start = body.find("(", pos)
        if paren_start == -1: break
        
        count = 1
        i = paren_start + 1
        while i < len(body) and count > 0:
            if body[i] == '(': count += 1
            elif body[i] == ')': count -= 1
            i += 1
        
        found_asserts.append(body[pos:i])
        start = i

    new_body = body
    new_body = re.sub(r'assert\s*:=\s*assert\.New\(t\)', '', new_body)

    for idx, old_stmt in enumerate(found_asserts):
        wrapped = f"""
    t.Run("Subtest_{idx}", func(t *testing.T) {{
        defer func() {{ recover() }}()
        asrt := assert.New(t)
        {old_stmt.replace("assert.", "asrt.")}
    }})"""
        new_body = new_body.replace(old_stmt, wrapped, 1)

    return f"func {func_name}(t *testing.T) {{\n{new_body}\n}}"

def build_go_code(test_setup, imports_field, helpers, program, wrapped_test):
    body_content = f"{helpers}\n{program}\n{wrapped_test}"
    all_raw_imports = f"{test_setup}\n{imports_field}\n{helpers}"
    import_paths = set(re.findall(r'"(.*?)"', all_raw_imports))

    final_imports = []
    for path in import_paths:
        alias = path.split('/')[-1]
        if re.search(rf'\b{alias}\b', body_content) or alias == "testing":
            final_imports.append(f'    "{path}"')
            
    final_code = ["package main"]
    if final_imports:
        final_code.append("import (")
        final_code.extend(sorted(final_imports))
        final_code.append(")")
    
    in_import_block = False
    for line in body_content.split('\n'):
        stripped = line.strip()
        if not stripped or stripped.startswith("package "): continue
        if stripped.startswith("import ("):
            in_import_block = True
            continue
        if in_import_block:
            if stripped == ")": in_import_block = False
            continue
        if stripped.startswith("import "): continue
            
        final_code.append(line)

    return "\n".join(final_code)

class GoExecutionRunner:
    def __init__(self, work_dir):
        self.work_dir = work_dir

    def prepare_and_run_base(self, full_code):
        file_path = os.path.join(self.work_dir, "solution_test.go")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(full_code)

        subprocess.run(["go", "mod", "init", "eval_project"], cwd=self.work_dir, capture_output=True)
        subprocess.run(["go", "mod", "tidy"], cwd=self.work_dir, capture_output=True)

        try:
            proc = subprocess.run(
                ["go", "test", "-v", "."], 
                cwd=self.work_dir, capture_output=True, text=True, timeout=TIMEOUT_SEC
            )
            stdout = proc.stdout
            
            pass_matches = re.findall(r'--- PASS: \w+/Subtest_\d+', stdout)
            fail_matches = re.findall(r'--- FAIL: \w+/Subtest_\d+', stdout)

            total = len(pass_matches) + len(fail_matches)
            score = len(pass_matches) / total if total > 0 else 0.0
            
            failures = [line for line in stdout.split('\n') if "--- FAIL:" in line]
            return score, stdout, failures
            
        except subprocess.TimeoutExpired:
            return 0.0, "", ["TIMEOUT"]
        except Exception as e:
            return 0.0, "", [str(e)]
        
    def prepare_and_compile_custom(self, full_code, entry_point, task_inputs):
        clean_body = re.sub(r'import\s+\((?:.|\n)*?\)', '', full_code, flags=re.DOTALL)
        lines = clean_body.split('\n')
        final_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("package ") or stripped.startswith("import "):
                continue
            if re.match(r'^func\s+main\s*\(', stripped):
                line = line.replace("func main", "func _snippet_main_internal", 1)
            final_lines.append(line)
        
        clean_snippet = "\n".join(final_lines).strip()

        pkg_mapping = {
            "math": ("math", "__std_math"),
            "strings": ("strings", "__std_strings"),
            "sort": ("sort", "__std_sort"),
            "strconv": ("strconv", "__std_strconv"),
            "unicode": ("unicode", "__std_unicode"),
            "fmt": ("fmt", "__std_fmt"),
            "os": ("os", "__std_os"),
            "time": ("time", "__std_time"),
            "regexp": ("regexp", "__std_regexp"),
            "bytes": ("bytes", "__std_bytes"),
            "md5": ("crypto/md5", "__std_md5"),
            "sha256": ("crypto/sha256", "__std_sha256"),
            "big": ("math/big", "__std_big"),
            "json": ("encoding/json", "__std_json"),
            "bits": ("math/bits", "__std_bits"),
            "rand": ("math/rand", "__std_rand")
        }
        
        needed_imports = []
        transformed_snippet = clean_snippet

        code_for_analysis = re.sub(r'//.*', '', clean_snippet)
        code_for_analysis = re.sub(r'/\*.*?\*/', '', code_for_analysis, flags=re.DOTALL)

        for name, (path, alias) in pkg_mapping.items():
            if re.search(rf'\b{name}\.', code_for_analysis):
                needed_imports.append(f'{alias} "{path}"')
                transformed_snippet = re.sub(rf'\b{name}\.', f'{alias}.', transformed_snippet)

        import_block = "import (\n    " + "\n    ".join(sorted(needed_imports)) + "\n)" if needed_imports else ""
        solution_content = f"package main\n\n{import_block}\n\n{transformed_snippet}"
        
        with open(os.path.join(self.work_dir, "Solution.go"), "w", encoding="utf-8") as f:
            f.write(solution_content)

        main_lines = [
            "package main",
            'import ("os"; "strconv"; "fmt")',
            "func main() {",
            "    if len(os.Args) < 2 { return }",
            "    index, _ := strconv.Atoi(os.Args[1])",
            "    _ = index",
            "    defer func() { if r := recover(); r != nil { fmt.Fprintf(os.Stderr, \"__EXCEPTION__: %v\\n\", r); os.Exit(1) } }()",
        ]
        for idx, inp in enumerate(task_inputs, 1):
            main_lines.append(f'    if index == {idx} {{ fmt.Printf("__RESULT__:%v__SUCCESS__\\n", {entry_point}({inp})) }}')
        main_lines.append("}")
        
        with open(os.path.join(self.work_dir, "Main.go"), "w", encoding="utf-8") as f:
            f.write("\n".join(main_lines))

        res = subprocess.run(['go', 'build', '-o', 'Main', 'Main.go', 'Solution.go'], 
                               cwd=self.work_dir, capture_output=True, text=True)
        return res.returncode == 0, res.stderr

    def run_all_custom(self, num_inputs):
        results = {}
        for idx in range(1, num_inputs + 1):
            try:
                proc = subprocess.run(["./Main", str(idx)], cwd=self.work_dir, 
                                       capture_output=True, text=True, timeout=TIMEOUT_SEC)
                if "__SUCCESS__" in proc.stdout:
                    val = proc.stdout.split("__RESULT__:")[1].split("__SUCCESS__")[0].strip()
                    results[idx] = {"ok": True, "val": val}
                else:
                    results[idx] = {"ok": False}
            except:
                results[idx] = {"ok": False}
        return results

# ==========================================
# Main Evaluation Logic
# ==========================================
def evaluate_single_task_go(item):
    global GO_TASKS, INPUTS_MAP, EVAL_MODE, LOG_ROOT
    full_task_id = item["task_id"]
    task_id = full_task_id.split("/")[-1]
    if task_id not in GO_TASKS: return item
    
    task = GO_TASKS[task_id]
    declaration = task.get("declaration", "").strip()
    entry_point = re.search(r'func\s+(\w+)\s*\(', declaration).group(1) if re.search(r'func\s+(\w+)\s*\(', declaration) else "solution"

    temp_dir = tempfile.mkdtemp()
    runner = GoExecutionRunner(temp_dir)

    # 1. Golden Result (EvalPlus 모드일 경우)
    golden_results = {}
    if EVAL_MODE == "custom":
        task_inputs = INPUTS_MAP.get(f"Go/{task_id}", [])
        golden_full = f"package main\n{task['import']}\n{task['prompt']}\n{task['canonical_solution']}"
        success_g, _ = runner.prepare_and_compile_custom(golden_full, entry_point, task_inputs)
        if success_g:
            golden_results = runner.run_all_custom(len(task_inputs))

    # 2. Iterate through each snippet (0, 1, 2...)
    for key in list(item.keys()):
        if not key.isdigit(): continue
        if f"grade-{key}" not in item: item[f"grade-{key}"] = {}

        snippet_code = item[key]
        log_dir = os.path.join(LOG_ROOT, task_id)
        os.makedirs(log_dir, exist_ok=True)
        snippet_log_path = os.path.join(log_dir, f"{key}.log")

        if EVAL_MODE == "base":
            wrapped_test = inject_subtests(task["test"])
            full_code = build_go_code(
                task.get("test_setup", "package main\nimport \"testing\""),
                task.get("import", ""),
                "", # Helpers can be added here if needed
                snippet_code,
                wrapped_test
            )
            score, stdout, failures = runner.prepare_and_run_base(full_code)
            item[f"grade-{key}"]["execution_base"] = score
            if failures:
                with open(snippet_log_path, "w", encoding="utf-8") as f:
                    f.write(f"Score: {score}\n\n{stdout}")
        
        else:
            task_inputs = INPUTS_MAP.get(f"Go/{task_id}", [])
            success_s, compile_err = runner.prepare_and_compile_custom(snippet_code, entry_point, task_inputs)
            
            if not success_s:
                with open(snippet_log_path, "w", encoding="utf-8") as f:
                    f.write(f"[COMPILE ERROR]\n{compile_err}")
                item[f"grade-{key}"]["execution_custom"] = 0.0
                continue

            pred_results = runner.run_all_custom(len(task_inputs))
            passed = 0
            failure_details = []
            for idx in range(1, len(task_inputs) + 1):
                gold = golden_results.get(idx)
                pred = pred_results.get(idx)
                if gold and gold["ok"] and pred and pred["ok"] and str(gold["val"]) == str(pred["val"]):
                    passed += 1
                else:
                    failure_details.append(
                        f"Input: {idx} | Input: {task_inputs[idx-1]}\nExpected: {gold}\nGot: {pred}\n{'-'*40}"
                    )
            if failure_details:
                with open(snippet_log_path, "w", encoding="utf-8") as f:
                    f.write(f"Snippet: {key} | Failures: {len(failure_details)}/{len(task_inputs)}\n\n")
                    f.write("\n".join(failure_details))
            score = passed / len(task_inputs) if task_inputs else 0.0
            item[f"grade-{key}"]["execution_custom"] = score

    shutil.rmtree(temp_dir)
    return item

def init_worker(mode, log_path):
    global GO_TASKS, INPUTS_MAP, EVAL_MODE, LOG_ROOT
    EVAL_MODE = mode
    LOG_ROOT = log_path
    GO_TASKS = {}
    with open(GO_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            data = json.loads(line)
            GO_TASKS[data["task_id"].split("/")[-1]] = data
    INPUTS_MAP = {}
    with open(INPUT_JSONL, 'r', encoding='utf-8') as f:
        for line in f:
            item = json.loads(line)
            INPUTS_MAP[item['task_id']] = item['inputs']

def atomic_save_json(data, path):
    dir_name = os.path.dirname(path)
    if dir_name: os.makedirs(dir_name, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=dir_name, delete=False, suffix=".tmp") as tmp:
        json.dump(data, tmp, indent=2)
        temp_name = tmp.name
    os.replace(temp_name, path)

# 경로 설정 (사용자 환경에 맞춰 수정 필요)
GO_FILE = "data/humaneval-x/go.jsonl"
INPUT_JSONL = "data/humaneval-x/go_inputs.jsonl"

GRADE_FILE = "data/humaneval-x/humaneval_go_grade_evalplus_ratio.json"
OUTPUT_JSON = "data/humaneval-x/humaneval_go_grade_evalplus_ratio.json"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, choices=["base", "custom"], default="base")
    args = parser.parse_args()

    curr_log_root = f"results/go_eval_{args.mode}"
    if os.path.exists(curr_log_root): shutil.rmtree(curr_log_root)
    os.makedirs(curr_log_root, exist_ok=True)

    with open(GRADE_FILE, "r") as f:
        data = json.load(f)

    print(f"Starting Go ICE-Score Evaluation | Mode: {args.mode}")
    with mp.Pool(processes=MAX_PARALLEL_TASKS, initializer=init_worker, initargs=(args.mode, curr_log_root)) as pool:
        for updated_task in tqdm(pool.imap_unordered(evaluate_single_task_go, data), total=len(data)):
            if updated_task:
                for i in range(len(data)):
                    if data[i]["task_id"] == updated_task["task_id"]:
                        data[i] = updated_task
                        break
                atomic_save_json(data, OUTPUT_JSON)

    print(f"Finished! Results saved to {OUTPUT_JSON}")

if __name__ == "__main__":
    main()