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

# humaneval-x "test" 필드에서 header, test_case분리
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

    # test에서 사용하는 assert 가로채서, pass, fail 로그 세는 식으로 rational 계산
    # loop도 한 iteration당 하나의 테스트케이스로 취급
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
            "// [Rational Counter]",
            "int __total_cnt = 0;",
            "int __passed_cnt = 0;",
            "// [Macro Interceptor]",
            "#undef assert",
            "#define assert(condition) do { \\",
            "    __total_cnt++; \\",
            "    try { \\",
            "        if (condition) { \\",
            "            __passed_cnt++; \\",
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
            "    if (__total_cnt == 0) std::cout << \"__RATIONAL_SCORE__:0.0\" << std::endl;",
            "    else std::cout << \"__RATIONAL_SCORE__:\" << (double)__passed_cnt / __total_cnt << std::endl;",
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
    
def evaluate_single_task_cpp(item):
    global CPP_TASKS, INPUTS_MAP, EVAL_MODE, LOG_ROOT
    full_task_id = item["task_id"]
    task_id = full_task_id.split("/")[-1]
    if task_id not in CPP_TASKS: 
        return item
    
    task = CPP_TASKS[task_id]
    task_inputs = INPUTS_MAP.get(f"CPP/{task_id}", [])
    if not task_inputs: return item

    declaration_head = task["declaration"]
    func_name = extract_cpp_func_name(declaration_head)

    temp_dir = tempfile.mkdtemp()
    runner = CppExecutionRunner(temp_dir)

    golden_code = build_cpp_code(declaration_head, task["canonical_solution"])
    runner.prepare_and_compile(golden_code, func_name, task_inputs)
    golden_results = runner.run_all(len(task_inputs))

    log_dir = os.path.join(LOG_ROOT, task_id)
    os.makedirs(log_dir, exist_ok=True)

    for key in list(item.keys()):
        if not key.isdigit(): continue
        if f"grade-{key}" not in item:
            item[f"grade-{key}"] = {}

        snippet_log_path = os.path.join(LOG_ROOT, task_id, f"{key}.log")
        snippet_code = build_cpp_code(declaration_head, item[key])

        if EVAL_MODE == "base":
            header, main_body = parse_base_test_cases(task["test"])
            if not main_body:
                item[f"grade-{key}"]["execution_base"] = 0.0
                continue
                
            success, compile_err = runner.prepare_and_compile_base_split(snippet_code, header, main_body)
            
            if not success:
                with open(snippet_log_path, "w", encoding="utf-8") as f:
                    f.write(f"[COMPILE ERROR]\n{compile_err}")
                item[f"grade-{key}"]["execution_base"] = 0.0
                continue
                
            score, all_logs, failures = runner.run_base_split()
            item[f"grade-{key}"]["execution_base"] = score

            if failures:
                with open(snippet_log_path, "w", encoding="utf-8") as f:
                    f.write(f"Snippet: {key} | Calculated Rational Score: {score}\n\n")
                    f.write("\n".join(failures))
        
        else:
            success, compile_err = runner.prepare_and_compile(snippet_code, func_name, task_inputs)
            if not success:
                with open(snippet_log_path, "w", encoding="utf-8") as f:
                    f.write(f"Snippet: {key} | [COMPILE ERROR]\n{compile_err}\n")
                item[f"grade-{key}"]["execution_custom"] = 0.0
                continue
                
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
                    f.write(f"Snippet: {key} | Failures: {len(failure_details)}/{len(task_inputs)}\n\n")
                    f.write("\n".join(failure_details))
            item[f"grade-{key}"]["execution_custom"] = passed / len(task_inputs)

    shutil.rmtree(temp_dir)
    return item

def init_worker(mode, log_path):
    global CPP_TASKS, INPUTS_MAP, EVAL_MODE, LOG_ROOT
    EVAL_MODE = mode
    LOG_ROOT = log_path

    CPP_TASKS = {}
    with open(CPP_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            data = json.loads(line)
            CPP_TASKS[data["task_id"].split("/")[-1]] = data
    
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


CPP_FILE = "data/humaneval-x/cpp.jsonl"
INPUT_JSONL = "data/humaneval-x/cpp_inputs.jsonl"

GRADE_FILE = "data/humaneval-x/humaneval_cpp_grade_evalplus_ratio.json"
OUTPUT_JSON = "data/humaneval-x/humaneval_cpp_grade_evalplus_ratio.json"
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, choices=["base", "custom"], default="base")
    args = parser.parse_args()

    curr_log_root = f"results/cpp_eval_{args.mode}"

    if os.path.exists(curr_log_root):
        print(f"Cleaning up old logs in {curr_log_root}...")
        shutil.rmtree(curr_log_root)
    os.makedirs(curr_log_root, exist_ok=True)

    if not os.path.exists(GRADE_FILE):
        print(f"Error: {GRADE_FILE} not found.")
        return
    
    if GRADE_FILE != OUTPUT_JSON:
        shutil.copy2(GRADE_FILE, OUTPUT_JSON)

    with open(OUTPUT_JSON, "r") as f:
        data = json.load(f)

    print(f"Starting C++ Execution Evaluation | Workers: {MAX_PARALLEL_TASKS}")
    
    with mp.Pool(processes=MAX_PARALLEL_TASKS, initializer=init_worker, initargs=(args.mode, curr_log_root)) as pool:
        for updated_task in tqdm(pool.imap_unordered(evaluate_single_task_cpp, data), total=len(data)):
            if updated_task:
                for i in range(len(data)):
                    if data[i]["task_id"] == updated_task["task_id"]:
                        data[i] = updated_task
                        break
                atomic_save_json(data, OUTPUT_JSON)

    print(f"Finished! Updated results saved to {OUTPUT_JSON}")

if __name__ == "__main__":
    main()