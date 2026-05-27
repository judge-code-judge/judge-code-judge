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
from config import *

TIMEOUT_SEC = 5
MAX_PARALLEL_TASKS = 16
CHUNK_SIZE = 100
CPP_TASKS = None
INPUTS_MAP = None

SERIALIZATION_HELPER = """
#include <iostream>
#include <sstream>
#include <vector>
#include <array>
#include <map>
#include <string>

template<typename T>
std::string toString(const T& value) {
    std::stringstream ss;
    ss << value;
    return ss.str();
}

template<typename T, size_t N>
std::string toString(const std::array<T, N>& arr) {
    std::stringstream ss;
    ss << "[";
    for (size_t i = 0; i < N; ++i) {
        ss << toString(arr[i]);
        if (i != N - 1) ss << ", ";
    }
    ss << "]";
    return ss.str();
}

template<typename T>
std::string toString(const std::vector<T>& vec) {
    std::stringstream ss;
    ss << "[";
    for (size_t i = 0; i < vec.size(); ++i) {
        ss << toString(vec[i]);
        if (i != vec.size() - 1) ss << ", ";
    }
    ss << "]";
    return ss.str();
}
"""

CONDA_PREFIX = os.environ.get('CONDA_PREFIX', '/usr')
BOOST_PATH = os.path.join(CONDA_PREFIX, 'include')

# ==========================================
# Serialization 및 Helper
# ==========================================
def extract_cpp_func_name(declaration):
    match = re.search(r'([\w<>\s:]+)\s+(\w+)\s*\(', declaration)
    return match.group(2) if match else "solution"

def build_cpp_code(head, body):
    full_code = head + "\n" + body
    open_cnt = full_code.count('{')
    close_cnt = full_code.count('}')
    if open_cnt > close_cnt:
        full_code += "\n}" * (open_cnt - close_cnt)
    return full_code

def parse_base_test_cases(test_code: str) -> Tuple[str, str]:
    main_match = re.search(r'int\s+main\s*\(\s*\)\s*\{', test_code)
    if not main_match: return "", ""
    
    header = test_code[:main_match.start()].strip()
    header = re.sub(r'#undef\s+NDEBUG|#include\s*<(assert\.h|cassert)>|using\s+namespace\s+std\s*;', '', header)

    main_body = test_code[main_match.end():].strip()
    if main_body.endswith('}'): main_body = main_body[:-1].strip()
    
    return header, main_body

class CppExecutionRunner:
    def __init__(self, work_dir):
        self.work_dir = work_dir

    def prepare_and_compile_base_split(self, solution_code, header, main_body):
        with open(os.path.join(self.work_dir, "Solution.cpp"), "w", encoding="utf-8") as f:
            f.write(solution_code)

        main_lines = [
            "#include <iostream>",
            "#include <vector>",
            "#include <string>",
            "#include <sstream>",
            "#include <optional>",
            "#include <algorithm>",
            "#include <numeric>",
            "#include <cmath>",
            "#include <climits>",
            "#include <stack>",
            "#include <map>",
            "#include <set>",
            "#include <stdexcept>",
            "#include <cassert>",
            "using namespace std;",
            "static int __t = 0;",
            "static int __p = 0;",

            "#undef assert",
            "#define assert(condition) do { \\",
            "    __t++; \\",
            "    try { \\",
            "        if (condition) { \\",
            "            __p++; \\",
            "            std::cout << \"__LOG__:PASS:\" << #condition << std::endl; \\",
            "        } else { \\",
            "            std::cout << \"__LOG__:FAIL:\" << #condition << std::endl; \\",
            "        } \\",
            "    } catch (...) { \\",
            "        std::cout << \"__LOG__:EXCEPTION:\" << #condition << std::endl; \\",
            "    } \\",
            "} while(0)",
            SERIALIZATION_HELPER,
            header,
            '#include "Solution.cpp"',
            "int main() {",
            "    try {",
            main_body,
            "    } catch (...) { std::cout << \"__CRITICAL_ERROR__\" << std::endl; }",
            "    if (__t == 0) std::cout << \"__RATIONAL_SCORE__:0.0\" << std::endl;",
            "    else std::cout << \"__RATIONAL_SCORE__:\" << (double)__p / __t << std::endl;",
            "    return 0;",
            "}"
        ]
        
        with open(os.path.join(self.work_dir, "Main.cpp"), "w", encoding="utf-8") as f:
            f.write("\n".join(main_lines))
            
        compile_cmd = ['g++', '-std=c++17', 'Main.cpp', '-o', 'Main', 
                       f'-I{BOOST_PATH}', f'-L{CONDA_PREFIX}/lib', '-lcrypto', '-lssl']
        res = subprocess.run(compile_cmd, cwd=self.work_dir, capture_output=True, text=True)
        return res.returncode == 0, res.stderr

    def run_base_split(self):
        try:
            proc = subprocess.run(["./Main"], cwd=self.work_dir, capture_output=True, text=True, timeout=TIMEOUT_SEC)
            
            score_match = re.search(r"__RATIONAL_SCORE__:([\d.]+)", proc.stdout)
            score = float(score_match.group(1)) if score_match else 0.0

            all_logs = [line for line in proc.stdout.split('\n') if line.startswith("__LOG__:")]
            failures = [l for l in all_logs if ":FAIL:" in l or ":EXCEPTION:" in l]
            
            return score, all_logs, failures
        except subprocess.TimeoutExpired:
            return 0.0, [], ["TIMEOUT: Execution exceeded limit"]
        except Exception as e:
            return 0.0, [], [f"RUNTIME ERROR: {str(e)}"]

    def prepare_and_compile(self, code, entry_point, task_inputs):
        # 보정된 코드를 Solution.cpp에 저장
        with open(os.path.join(self.work_dir, "Solution.cpp"), "w", encoding="utf-8") as f:
            f.write(code)
            
        main_lines = [
            "#include <iostream>",
            "#include <vector>",
            "#include <string>",
            "#include <sstream>",
            "#include <boost/any.hpp>",
            "#include <stdexcept>",
            "#include <cassert>",
            "#include <climits>",
            SERIALIZATION_HELPER,
            '#include "Solution.cpp"',
            "int main(int argc, char** argv) {",
            "    if(argc < 2) return 1;",
            "    int index = std::stoi(argv[1]);",
            "    try {"
        ]
        
        for i in range(0, len(task_inputs), CHUNK_SIZE):
            start, end = i + 1, min(i + CHUNK_SIZE, len(task_inputs))
            main_lines.append(f"        if (index >= {start} && index <= {end}) {{")
            for idx in range(start, end + 1):
                inp = task_inputs[idx-1]
                main_lines.append(f'            if (index == {idx}) {{ std::cout << "__RESULT__:" << toString({entry_point}({inp})) << "__SUCCESS__" << std::endl; }}')
            main_lines.append("        }")
            
        main_lines.extend(["    } catch (...) { return 1; }", "    return 0;", "}"])
        
        with open(os.path.join(self.work_dir, "Main.cpp"), "w", encoding="utf-8") as f:
            f.write("\n".join(main_lines))
            
        compile_cmd = [
            'g++', '-std=c++17', 'Main.cpp', '-o', 'Main',
            f'-I{BOOST_PATH}', f'-L{CONDA_PREFIX}/lib',
            '-lcrypto', '-lssl'
        ]
        res = subprocess.run(compile_cmd, cwd=self.work_dir, capture_output=True, text=True)
        return res.returncode == 0, res.stderr

    def run_all(self, num_inputs):
        results = {}
        binary_path = os.path.join(self.work_dir, "Main")
        if not os.path.exists(binary_path):
            return {idx: {"ok": False, "error": "Compile failed"} for idx in range(1, num_inputs + 1)}

        for idx in range(1, num_inputs + 1):
            try:
                proc = subprocess.run(
                    ["./Main", str(idx)], 
                    cwd=self.work_dir, 
                    capture_output=True, 
                    text=True, 
                    errors='replace',
                    timeout=TIMEOUT_SEC
                )
                if "__SUCCESS__" in proc.stdout:
                    res_str = proc.stdout.split("__RESULT__:")[1].split("__SUCCESS__")[0].strip()
                    results[idx] = {"ok": True, "val": res_str}
                else:
                    results[idx] = {"ok": False, "error": proc.stderr or "Runtime Error"}
            except subprocess.TimeoutExpired:
                results[idx] = {"ok": False, "error": "TIMEOUT"}
        return results
    
    def prepare_and_compile_base_individual(self, solution_code, header, assert_cases):
        with open(os.path.join(self.work_dir, "Solution.cpp"), "w", encoding="utf-8") as f:
            f.write(solution_code)
            
        main_lines = [
            "#include <iostream>", "#include <vector>", "#include <string>", "#include <cassert>",
            "using namespace std;",
            header,
            '#include "Solution.cpp"',
            "int main(int argc, char** argv) {",
            "    if(argc < 2) return 1;",
            "    int target_idx = std::stoi(argv[1]);",
            "    try {"
        ]
        
        for idx, case in enumerate(assert_cases):
            main_lines.append(f"        if (target_idx == {idx}) {{ {case} std::cout << \"__SUCCESS__\" << std::endl; }}")
            
        main_lines.extend([
            "    } catch (...) { return 1; }",
            "    return 0;",
            "}"
        ])
        
        with open(os.path.join(self.work_dir, "Main_Indiv.cpp"), "w", encoding="utf-8") as f:
            f.write("\n".join(main_lines))
            
        cmd = [
            'g++', '-std=c++17', 'Main_Indiv.cpp', '-o', 'Main_Indiv',
            f'-I{BOOST_PATH}', f'-L{CONDA_PREFIX}/lib',
            '-lcrypto', '-lssl'
        ]
        res = subprocess.run(cmd, cwd=self.work_dir, capture_output=True, text=True)
        return res.returncode == 0

    def run_base_individual_case(self, index):
        try:
            proc = subprocess.run(
                ["./Main_Indiv", str(index)],
                cwd=self.work_dir, capture_output=True, text=True, timeout=TIMEOUT_SEC
            )
            return "__SUCCESS__" in proc.stdout
        except:
            return False
    
def evaluate_sample(item):
    global CPP_TASKS, INPUTS_MAP, EVAL_MODE, LOG_ROOT
    task_id = item.get("question_id")
    if task_id not in CPP_TASKS: return item

    task = CPP_TASKS[task_id]
    declaration_head = task["declaration"]
    func_name = extract_cpp_func_name(declaration_head)

    temp_dir = tempfile.mkdtemp()
    runner = CppExecutionRunner(temp_dir)

    snippet_log_path = os.path.join(LOG_ROOT, f"{task_id}.log")

    if "#include" in item["program"] or func_name in item["program"]:
        snippet_code = build_cpp_code("", item["program"])
    else:
        snippet_code = build_cpp_code(declaration_head, item["program"])

    if EVAL_MODE == "base":
        header, main_body = parse_base_test_cases(task["test"])
        if not main_body:
            item["pass_ratio"] = 0.0
            shutil.rmtree(temp_dir)
            return item
            
        success, compile_err = runner.prepare_and_compile_base_split(snippet_code, header, main_body)
        
        if not success:
            with open(snippet_log_path, "w", encoding="utf-8") as f:
                f.write(f"Task: {task_id} | [COMPILE ERROR]\n{compile_err}")
            item["pass_ratio"] = 0.0
        else:
            score, all_logs, failures = runner.run_base_split()

            if score < 1.0 or not all_logs:
                assert_cases = re.findall(r'assert\s*\(.*?\)\s*;', main_body, re.DOTALL)
                
                if len(assert_cases) > 0:
                    if runner.prepare_and_compile_base_individual(snippet_code, header, assert_cases):
                        passed_count = 0
                        for i in range(len(assert_cases)):
                            if runner.run_base_individual_case(i):
                                passed_count += 1
                        score = passed_count / len(assert_cases)

            item["pass_ratio"] = score

            if score < 1.0 or failures:
                with open(snippet_log_path, "w", encoding="utf-8") as f:
                    f.write(f"Task: {task_id} | Calculated Rational Score: {score}\n\n")
                    if failures:
                        f.write("\n".join(failures))
                    else:
                        f.write("No assert logs found. Possible issue with test case parsing or execution.")

    else:
        task_inputs = INPUTS_MAP.get(f"CPP/{task_id}", [])
        golden_code = build_cpp_code(declaration_head, task["canonical_solution"])
        runner.prepare_and_compile(golden_code, func_name, task_inputs)
        golden_results = runner.run_all(len(task_inputs))
        success, compile_err = runner.prepare_and_compile(snippet_code, func_name, task_inputs)

        if not success:
            with open(snippet_log_path, "w", encoding="utf-8") as f:
                f.write(f"Task: {task_id} | [COMPILE ERROR]\n{compile_err}\n")
            item["pass_ratio_evalplus"] = 0.0
        else:
            pred_results = runner.run_all(len(task_inputs))
            passed = 0
            failure_details = []
            for idx in range(1, len(task_inputs) + 1):
                gold, pred = golden_results.get(idx), pred_results.get(idx)
                if gold and gold["ok"] and pred and pred["ok"] and gold["val"] == pred["val"]:
                    passed += 1
                else:
                    failure_details.append(
                        f"Index: {idx} | Input: {task_inputs[idx-1]}\nExpected: {gold}\nGot: {pred}\n{'-'*40}"
                    )
            if failure_details:
                with open(snippet_log_path, "w", encoding="utf-8") as f:
                    f.write(f"Task: {task_id} | Failures: {len(failure_details)}/{len(task_inputs)}\n\n")
                    f.write("\n".join(failure_details))
            item["pass_ratio_evalplus"] = passed / len(task_inputs)

    shutil.rmtree(temp_dir)
    return item

def init_worker(mode, log_path):
    global CPP_TASKS, INPUTS_MAP, EVAL_MODE, LOG_ROOT
    EVAL_MODE = mode
    LOG_ROOT = log_path

    CPP_TASKS = {}
    with open(HEX_CPP_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            data = json.loads(line)
            CPP_TASKS[data["task_id"].split("/")[-1]] = data
    
    INPUTS_MAP = {}
    with open(HEX_CPP_INPUT, 'r', encoding='utf-8') as f:
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
    os.path.join(RESULT_ROOT, "cpp", "gpt-3.5-turbo-1106-1-0-0.0-sample-0.json"),  # small-test set 
    # os.path.join(RESULT_ROOT, "cpp", "gpt-3.5-turbo-1106-2-0-0-0.0-sample-0.json") # small-validation set
]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, choices=["base", "evalplus"], default="base")
    args = parser.parse_args()

    curr_log_root = os.path.join(LOG_ROOT, "cpp", args.mode)
    if os.path.exists(curr_log_root): 
        shutil.rmtree(curr_log_root)
    os.makedirs(curr_log_root, exist_ok=True)

    total_results = {}

    for grade_file in GRADE_FILES:
        summary_data = process_single_grade_file(grade_file, args.mode, curr_log_root)
        total_results.update(summary_data)

    suffix = "base" if args.mode == "base" else "plus"
    output_path = f"humaneval_cpp_codejudge_{suffix}_ratio.json"
    output_path = os.path.join(RESULT_ROOT, "cpp", output_path)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(total_results, f, indent=4)

    print(f"Summary of Rational Scores saved to {output_path}")

if __name__ == "__main__":
    main()