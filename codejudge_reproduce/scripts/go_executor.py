import os
import json
import subprocess
import re
import shutil
import tempfile
import argparse
import multiprocessing as mp
from tqdm import tqdm
from config import *

TIMEOUT_SEC = 5
MAX_PARALLEL_TASKS = 16

GO_TASKS = None
INPUTS_MAP = None

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
        if not stripped:
            final_code.append(line)
            continue
            
        if stripped.startswith("package "):
            continue
        
        if stripped.startswith("import ("):
            in_import_block = True
            continue
        if in_import_block:
            if stripped == ")":
                in_import_block = False
            continue
            
        if stripped.startswith("import "):
            continue
            
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
                cwd=self.work_dir, 
                capture_output=True,
                text=True, 
                timeout=TIMEOUT_SEC
            )
            
            stdout = proc.stdout
            stderr = proc.stderr
            
            if proc.returncode != 0 and not stdout:
                stdout = f"--- COMPILATION ERROR ---\n{stderr}"
            elif stderr:
                stdout += f"\n--- STDERR ---\n{stderr}"

            pass_matches = re.findall(r'--- PASS: \w+/Subtest_\d+', stdout)
            fail_matches = re.findall(r'--- FAIL: \w+/Subtest_\d+', stdout)

            failure_details = []
            if fail_matches or (proc.returncode != 0 and not pass_matches):
                log_lines = stdout.split('\n')
                recording = False
                temp_log = []
                for line in log_lines:
                    if "--- FAIL:" in line:
                        recording = True
                        temp_log.append(line)
                    elif recording:
                        if line.startswith("===") or line.startswith("---") or line.startswith("FAIL"):
                            failure_details.append("\n".join(temp_log))
                            temp_log = []
                            recording = False
                        else:
                            temp_log.append(line)
                if temp_log: failure_details.append("\n".join(temp_log))
                if not failure_details and proc.returncode != 0:
                    failure_details.append(stdout)

            total = len(pass_matches) + len(fail_matches)
            score = len(pass_matches) / total if total > 0 else 0.0
            
            return score, stdout, failure_details
            
        except subprocess.TimeoutExpired:
            return 0.0, "", ["TIMEOUT: Execution exceeded limit"]
        except Exception as e:
            return 0.0, "", [f"RUNTIME ERROR: {str(e)}"]

    def prepare_and_compile_evalplus(self, full_code, entry_point, task_inputs, helpers):
        code_for_analysis = re.sub(r'//.*', '', full_code)
        code_for_analysis = re.sub(r'/\*.*?\*/', '', code_for_analysis, flags=re.DOTALL)

        pkg_mapping = {
            "math": "math", "strings": "strings", "sort": "sort", "strconv": "strconv",
            "unicode": "unicode", "fmt": "fmt", "os": "os", "time": "time",
            "regexp": "regexp", "bytes": "bytes", "md5": "crypto/md5", "sha256": "crypto/sha256",
            "big": "math/big", "json": "encoding/json", "bits": "math/bits", "rand": "math/rand"
        }
        
        needed_paths = []
        for name, path in pkg_mapping.items():
            if re.search(rf'\b{name}\.', code_for_analysis):
                needed_paths.append(f'"{path}"')
        
        clean_body = re.sub(r'^package\s+\w+\s*', '', full_code, flags=re.MULTILINE)
        clean_body = re.sub(r'import\s+\((?:.|\n)*?\)', '', clean_body)
        clean_body = re.sub(r'import\s+"[^"]+"', '', clean_body)
        
        import_block = "import (\n    " + "\n    ".join(needed_paths) + "\n)" if needed_paths else ""
        solution_content = f"package main\n\n{import_block}\n\n{clean_body.strip()}"
        
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

        res = subprocess.run(['go', 'build', '-o', 'Main', 'Main.go', 'Solution.go'], cwd=self.work_dir, capture_output=True, text=True)
        return res.returncode == 0, res.stderr

    def run_all_evalplus(self, num_inputs):
        results = {}
        for idx in range(1, num_inputs + 1):
            try:
                proc = subprocess.run(
                    ["./Main", str(idx)], 
                    cwd=self.work_dir, 
                    capture_output=True, 
                    text=True, 
                    encoding='utf-8',
                    errors='replace',
                    timeout=TIMEOUT_SEC
                )
                
                if "__SUCCESS__" in proc.stdout:
                    res_val = proc.stdout.split("__RESULT__:")[1].split("__SUCCESS__")[0].strip()
                    results[idx] = {"ok": True, "val": res_val}
                else:
                    results[idx] = {"ok": False, "error": proc.stderr or "Runtime Error"}
            except subprocess.TimeoutExpired:
                results[idx] = {"ok": False, "error": "TIMEOUT"}
            except Exception as e:
                results[idx] = {"ok": False, "error": str(e)}
        return results
    
# def wrap_with_declaration(code, decl):
#         if not code: return ""
#         code_stripped = code.strip()
        
#         func_name_match = re.search(r'func\s+(\w+)\s*\(', decl)
#         if not func_name_match:
#             return code_stripped
            
#         fn_name = func_name_match.group(1)
        
#         if f"func {fn_name}" in code_stripped:
#             if "{" in code_stripped and not code_stripped.endswith("}"):
#                 return code_stripped + "\n}"
#             return code_stripped

#         if code_stripped.startswith("{") and code_stripped.endswith("}"):
#             return f"{decl} {code_stripped}"
#         return f"{decl} {{\n{code_stripped}\n}}"  
  
def evaluate_sample(item):
    global GO_TASKS, INPUTS_MAP, EVAL_MODE, LOG_ROOT
    task_id = item.get("question_id")
    if task_id not in GO_TASKS: return item

    task = GO_TASKS[task_id]
    declaration = task.get("declaration", "").strip()
    prompt = task.get("prompt", "")

    helpers = ""
    if declaration in prompt:
        potential_helpers = prompt.split(declaration)[0]
        if "func " in potential_helpers:
            helpers = potential_helpers

    temp_dir = tempfile.mkdtemp()
    runner = GoExecutionRunner(temp_dir)
    snippet_log_path = os.path.join(LOG_ROOT, f"{task_id}.log")

    if EVAL_MODE == "base":
        wrapped_test = inject_subtests(task["test"])
        full_code = build_go_code(
            task.get("test_setup", "package main\nimport \"testing\""),
            task.get("import", ""),
            helpers,
            item["program"],
            wrapped_test
        )

        score, stdout, failures = runner.prepare_and_run_base(full_code)
        item["pass_ratio"] = score
        
        if score < 1.0:
            with open(snippet_log_path, "w", encoding="utf-8") as f:
                f.write(f"Task: {task_id} | Rational Score: {score:.4f}\n")
                f.write("-" * 50 + "\n")
                
                if failures:
                    f.write("### [DETAILED FAILURES] ###\n")
                    for fail in failures:
                        f.write(fail.strip() + "\n")
                elif "COMPILATION ERROR" in stdout:
                    f.write("### [COMPILATION ERROR] ###\n")
                    f.write(stdout)
                else:
                    f.write("### [EXECUTION LOG] ###\n")
                    f.write(stdout)
    else:
        task_inputs = INPUTS_MAP.get(f"Go/{task_id}", [])
        entry_point = re.search(r'func\s+(\w+)\s*\(', declaration).group(1) if re.search(r'func\s+(\w+)\s*\(', declaration) else "solution"
        
        golden_solution = f"{task['prompt']}\n{task['canonical_solution']}"
        target_program = f"{task['prompt']}\n{item['program']}" if f"func {entry_point}" not in item['program'] else item['program']

        success_g, err_g = runner.prepare_and_compile_evalplus(golden_solution, entry_point, task_inputs, helpers)
        if not success_g:
            with open(snippet_log_path, "w", encoding="utf-8") as f:
                f.write(f"Task: {task_id} | [GOLDEN COMPILE ERROR]\n{err_g}")
            item["pass_ratio_evalplus"] = 0.0
        else:
            golden_results = runner.run_all_evalplus(len(task_inputs))
            
            success_s, err_s = runner.prepare_and_compile_evalplus(target_program, entry_point, task_inputs, helpers)
            if not success_s:
                with open(snippet_log_path, "w", encoding="utf-8") as f:
                    f.write(f"Task: {task_id} | [USER CODE COMPILE ERROR]\n{err_s}")
                item["pass_ratio_evalplus"] = 0.0
            else:
                pred_results = runner.run_all_evalplus(len(task_inputs))
                passed = 0
                fail_details = []
                for idx in range(1, len(task_inputs) + 1):
                    gold, pred = golden_results.get(idx), pred_results.get(idx)
                    if gold and gold["ok"] and pred and pred["ok"] and str(gold["val"]) == str(pred["val"]):
                        passed += 1
                    else:
                        fail_details.append(f"Idx: {idx}\nExpected: {gold}\nGot: {pred}")

                with open(snippet_log_path, "w", encoding="utf-8") as f:
                    f.write(f"Task: {task_id} | Result: {passed}/{len(task_inputs)}\n")
                    if fail_details:
                        f.write("\n".join(fail_details))
                
                item["pass_ratio_evalplus"] = passed / len(task_inputs) if task_inputs else 0.0

    shutil.rmtree(temp_dir)
    return item

def init_worker(mode, log_path):
    global GO_TASKS, INPUTS_MAP, EVAL_MODE, LOG_ROOT
    EVAL_MODE = mode
    LOG_ROOT = log_path

    GO_TASKS = {}
    with open(HEX_GO_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            data = json.loads(line)
            GO_TASKS[data["task_id"].split("/")[-1]] = data
    
    INPUTS_MAP = {}
    with open(HEX_GO_INPUT, 'r', encoding='utf-8') as f:
        for line in f:
            item = json.loads(line)
            INPUTS_MAP[item['task_id']] = item['inputs']

def process_single_grade_file(file_path, mode, log_root):
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not Exists")
        return {}

    with open(file_path, "r", encoding="utf-8") as f:
        full_data = json.load(f)

    samples = full_data.get("data", [])
    file_name = os.path.basename(file_path)
    print(f"\n[Starting] {file_name} | Samples: {len(samples)}")

    with mp.Pool(processes=MAX_PARALLEL_TASKS, initializer=init_worker, initargs=(mode, log_root)) as pool:
        updated_samples = list(tqdm(pool.imap(evaluate_sample, samples), total=len(samples)))

    priority_keys = [
        "pass", "pass_ratio", "pass_ratio_evalplus", "program", 
        "canonical_solution", "code_gpt_score", "question_id"
    ]
    
    ordered_samples = []
    summary_data = {}

    ratio_key = "pass_ratio" if mode == "base" else "pass_ratio_evalplus"

    for item in updated_samples:
        ordered_item = {k: item[k] for k in priority_keys if k in item}
        for k, v in item.items():
            if k not in ordered_item:
                ordered_item[k] = v
        ordered_samples.append(ordered_item)

        q_id = str(item.get("question_id", ""))
        ratio = item.get(ratio_key)
        if q_id and ratio is not None:
            summary_data[q_id] = ratio

    full_data["data"] = ordered_samples

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(full_data, f, indent=4)
    print(f"[Finished] Results saved to {file_path}")

    return summary_data

GRADE_FILES = [
    os.path.join(RESULT_ROOT, "go", "gpt-3.5-turbo-1106-1-0-0.0-sample-0.json"),  # small-test set 
    # os.path.join(RESULT_ROOT, "go", "gpt-3.5-turbo-1106-2-0-0-0.0-sample-0.json") # small-validation set(Not exists for GO)
]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, choices=["base", "evalplus"], default="base")
    args = parser.parse_args()

    curr_log_root = os.path.join(LOG_ROOT, "go", args.mode)
    if os.path.exists(curr_log_root): 
        shutil.rmtree(curr_log_root)
    os.makedirs(curr_log_root, exist_ok=True)

    total_results = {}

    for grade_file in GRADE_FILES:
        summary_data = process_single_grade_file(grade_file, args.mode, curr_log_root)
        total_results.update(summary_data)

    suffix = "base" if args.mode == "base" else "plus"
    output_path = f"humaneval_go_codejudge_{suffix}_ratio.json"
    output_path = os.path.join(RESULT_ROOT, "go", output_path)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(total_results, f, indent=4)

    print(f"Summary of Rational Scores saved to {output_path}")

if __name__ == "__main__":
    main()