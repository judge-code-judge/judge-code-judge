import os
import json
import subprocess
import re
import shutil
import tempfile
import argparse
import multiprocessing as mp
from tqdm import tqdm
from typing import List

from config import *

TIMEOUT_SEC = 5
MAX_PARALLEL_TASKS = 16
CHUNK_SIZE = 100
JAVA_TASKS = None
INPUTS_MAP = None

STANDARD_IMPORTS = """
import java.util.*;
import java.math.*;
import java.io.*;
import java.util.stream.*;
"""

def extract_java_func_name(declaration):
    match = re.search(r'public\s+[\w<>, \[]+\s+(\w+)\s*\(', declaration)
    if not match:
        raise ValueError(f"Cannot parse function name from: {declaration}")
    return match.group(1)

def build_java_code(head, body):
    full_code = STANDARD_IMPORTS + "\n" + head + "\n" + body
    open_cnt = full_code.count('{')
    close_cnt = full_code.count('}')
    if open_cnt > close_cnt:
        full_code += "\n" + "}" * (open_cnt - close_cnt)
    return full_code

def get_balanced_expressions(text):
    exprs = []
    current = []
    depth = 0
    in_quotes = False
    i = 0
    while i < len(text):
        char = text[i]
        if char == '"' and (i == 0 or text[i-1] != '\\'):
            in_quotes = not in_quotes
        
        if not in_quotes:
            if char == '(': depth += 1
            elif char == ')': depth -= 1
            
            if char == ',' and depth == 0:
                exprs.append("".join(current).strip())
                current = []
                i += 1
                continue
        current.append(char)
        i += 1
    if current:
        exprs.append("".join(current).strip())
    return exprs

## ADD create map helper
def get_serialization_helper():
    return """
    public static String serialize(Object obj) {
        if (obj == null) return "null";
        if (obj instanceof String) return "\\"" + obj + "\\"";
        if (obj instanceof Double || obj instanceof Float) {
            return String.format("%.4f", ((Number)obj).doubleValue());
        }
        if (obj instanceof Object[]) return java.util.Arrays.deepToString((Object[])obj);
        if (obj instanceof java.util.Collection) {
            return java.util.Arrays.deepToString(((java.util.Collection<?>)obj).toArray());
        }
        return obj.toString();
    }

    public static <K, V> Map<K, V> createMap(List<K> keys, List<V> values) {
        if (keys.size() != values.size()) {
            throw new IllegalArgumentException("The sizes of the input lists must be the same.");
        }

        Map<K, V> map = new HashMap<>();
        for (int i = 0; i < keys.size(); i++) {
            K key = keys.get(i);
            V value = values.get(i);
            map.put(key, value);
        }
        return map;
    }
    """
# for TASK 32,38,50 - programmatic test cases with if(!condition){ throw new AssertionError(); } pattern
def instrument_programmatic_tests(text):
    pos = 0
    while True:
        match = re.search(r'if\s*\(\s*!', text[pos:])
        if not match: break
        
        start_idx = pos + match.start()
        paren_start = start_idx + text[start_idx:].find('(')
        
        depth, curr, in_q = 0, paren_start, False
        found_end = False
        while curr < len(text):
            c = text[curr]
            if c == '"' and (curr == 0 or text[curr-1] != '\\'): in_q = not in_q
            if not in_q:
                if c == '(': depth += 1
                elif c == ')': depth -= 1
            if depth == 0: 
                found_end = True
                break
            curr += 1
        
        if not found_end: break

        expr_with_parens = text[paren_start:curr+1]
        inner = expr_with_parens[1:-1].strip()
        if inner.startswith('!'):
            actual_condition = inner[1:].strip()
            after_if = text[curr+1:].strip()
            block_match = re.match(r'\{\s*throw\s+new\s+AssertionError\(.*?\)\s*;?\s*\}', after_if, re.DOTALL)
            
            if block_match:
                replacement = f'{{ __t++; try {{ if ({actual_condition}) {{ __p++; System.out.println("__LOG__:PASS:loop_case"); }} else {{ System.out.println("__LOG__:FAIL:loop_case"); }} }} catch (Throwable e) {{ System.out.println("__LOG__:EXCEPTION:loop_case"); }} }}'
                block_abs_start = curr + 1 + text[curr+1:].find('{')
                block_abs_end = block_abs_start + len(block_match.group(0))
                
                text = text[:start_idx] + replacement + text[block_abs_end:]
                pos = start_idx + len(replacement)
                continue
        pos = curr + 1
    return text

class JavaExecutionRunner:
    def __init__(self, work_dir):
        self.work_dir = work_dir
    
    def prepare_and_compile_base_split(self, snippet_code, test_code):
        main_match = re.search(
            r'public\s+static\s+void\s+main\s*\(\s*String\s*(\[\]\s*\w+|\w+\s*\[\])\s*\)(\s+throws\s+[\w\s,]+)?\s*\{', 
            test_code
        )
        if not main_match:
            return False, "Main method not found"
        
        header = test_code[:main_match.start()].strip()
        body = test_code[main_match.end():].rstrip()

        for _ in range(2):
            body = body.rstrip()
            if body.endswith('}'):
                body = body[:-1]
        body = body.rstrip()

        combined_extra = snippet_code + "\n" + header
        all_imports = set(re.findall(r'^import\s+[\w\.]+.*;', combined_extra, re.MULTILINE))
        all_imports.update([
            "import java.util.*;", "import java.math.*;", "import java.io.*;",
            "import java.util.stream.*;", "import java.util.function.*;"
        ])

        clean_snippet = re.sub(r'^import\s+[\w\.]+.*;', '', snippet_code, flags=re.MULTILINE)
        clean_header = re.sub(r'^import\s+[\w\.]+.*;', '', header, flags=re.MULTILINE)
        clean_header = re.sub(r'public\s+class\s+Main\s*\{', '', clean_header).strip()

        list_match = re.search(r'Arrays\.asList\s*\(', body)
        static_exprs = []
        if list_match:
            setup_code = body[:list_match.start()].strip()
            setup_code = re.sub(r'List<Boolean>\s+\w+\s*=\s*$', '', setup_code).strip()
            
            content_start = list_match.end()
            depth, curr, in_q = 1, content_start, False
            while curr < len(body) and depth > 0:
                c = body[curr]
                if c == '"' and (curr == 0 or body[curr-1] != '\\'): in_q = not in_q
                if not in_q:
                    if c == '(': depth += 1
                    elif c == ')': depth -= 1
                curr += 1
            
            static_content = body[content_start:curr-1]
            static_exprs = get_balanced_expressions(static_content)
            transformed_body = body[curr:].strip()
            transformed_body = re.sub(
                r'if\s*\(\s*!(.*?)\)\s*\{\s*throw\s+new\s+AssertionError\(.*?\)\s*;?\s*\}',
                r'''{ 
                    __t++; 
                    try { 
                        if (\1) { 
                            __p++; 
                            System.out.println("__LOG__:PASS:loop_case"); 
                        } else { 
                            System.out.println("__LOG__:FAIL:loop_case"); 
                        } 
                    } catch (Throwable e) { 
                        System.out.println("__LOG__:EXCEPTION:loop_case -> " + e.toString()); 
                    } 
                }''', 
                transformed_body, flags=re.DOTALL
            )
            
            transformed_body = re.sub(
                r'assert\s+(.*?)\s*;', 
                r'''{ 
                    __t++; 
                    try { 
                        if (\1) { 
                            __p++; 
                            System.out.println("__LOG__:PASS:assert_case"); 
                        } else { 
                            System.out.println("__LOG__:FAIL:assert_case"); 
                        } 
                    } catch (Throwable e) { 
                        System.out.println("__LOG__:EXCEPTION:assert_case -> " + e.toString()); 
                    } 
                }''', 
                transformed_body
            )

            transformed_body = re.sub(r'if\s*\(.*?.contains\(false\)\)\s*\{.*?\}', '', transformed_body, flags=re.DOTALL)
        
        else:
            setup_code = ""
            transformed_body = instrument_programmatic_tests(body)

        static_calls = []
        for c in static_exprs:
            call = f'''{{
                __t++;
                try {{
                    if ({c}) {{
                        __p++;
                        System.out.println("__LOG__:PASS:static");
                    }} else {{
                        System.out.println("__LOG__:FAIL:static");
                    }}
                }} catch (Throwable e) {{
                    System.out.println("__LOG__:EXCEPTION:static -> " + e.toString());
                }}
            }}'''
            static_calls.append(call)

        main_java = f"""
{chr(10).join(all_imports)}

public class Main {{
    {clean_header}
    private static int __t = 0;
    private static int __p = 0;

    public static void main(String[] args) {{
        try {{
            {setup_code}
    
            {chr(10).join(static_calls)}
            
            {transformed_body}
            
        }} catch (Throwable e) {{
            System.out.println("__LOG__:CRITICAL_ERROR:" + e.toString());
        }}
        
        if (__t == 0) System.out.println("__RATIONAL_SCORE__:0.0");
        else System.out.println("__RATIONAL_SCORE__:" + (double)__p / __t);
    }}
}}

{clean_snippet}
"""
        with open(os.path.join(self.work_dir, "Main.java"), "w", encoding="utf-8") as f:
            f.write(main_java)
        
        res = subprocess.run(["javac", "Main.java"], cwd=self.work_dir, capture_output=True, text=True)
        return res.returncode == 0, res.stderr
    
    def run_base_split(self):
        try:
            proc = subprocess.run(
                ["java", "Main"], 
                cwd=self.work_dir, 
                capture_output=True, 
                text=True, 
                timeout=TIMEOUT_SEC
            )
            
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
        with open(os.path.join(self.work_dir, "Solution.java"), "w", encoding="utf-8") as f:
            f.write(code)
            

        imports = STANDARD_IMPORTS
        main_body = [
            "public class Main {",
            get_serialization_helper(),
            "    public static void main(String[] args) throws Throwable {",
            "        int index = Integer.parseInt(args[0]);",
            "        Solution s = new Solution();",
            "        try {"
        ]
        
        chunk_methods = []
        for i in range(0, len(task_inputs), CHUNK_SIZE):
            c_idx = (i // CHUNK_SIZE) + 1
            start, end = i + 1, min(i + CHUNK_SIZE, len(task_inputs))
            main_body.append(f"            if (index >= {start} && index <= {end}) runChunk{c_idx}(s, index);")
            
            m_lines = [f"    private static void runChunk{c_idx}(Solution s, int index) throws Throwable {{"]
            for idx in range(start, end + 1):
                inp = task_inputs[idx-1]
                m_lines.append(f"        if (index == {idx}) {{ System.out.print(\"__RESULT__:\"); System.out.println(serialize(s.{entry_point}({inp}))); System.out.println(\"__SUCCESS__\"); }}")
            m_lines.append("    }")
            chunk_methods.append("\n".join(m_lines))
            
        main_body.extend(["        } catch (Throwable t) { System.err.println(\"__EXCEPTION__:\" + t.toString()); System.exit(1); }", "    }"])
        full_main = imports + "\n" + "\n".join(main_body) + "\n" + "\n".join(chunk_methods) + "\n}"
        
        with open(os.path.join(self.work_dir, "Main.java"), "w", encoding="utf-8") as f:
            f.write(full_main)
            
        res = subprocess.run(["javac", "Main.java", "Solution.java"], cwd=self.work_dir, capture_output=True, text=True)
        return res.returncode == 0, res.stderr

    def run_all(self, num_inputs):
        results = {}
        for idx in range(1, num_inputs + 1):
            try:
                proc = subprocess.run(["java", "Main", str(idx)], cwd=self.work_dir, capture_output=True, text=True, timeout=TIMEOUT_SEC)
                if "__SUCCESS__" in proc.stdout:
                    res_str = proc.stdout.split("__RESULT__:")[1].split("__SUCCESS__")[0].strip()
                    results[idx] = {"ok": True, "val": res_str}
                else:
                    results[idx] = {"ok": False, "error": proc.stderr or "Unknown Error"}
            except subprocess.TimeoutExpired:
                results[idx] = {"ok": False, "error": "TIMEOUT"}
        return results


def evaluate_sample(item):
    global JAVA_TASKS, INPUTS_MAP, EVAL_MODE, LOG_ROOT
    task_id = item.get("question_id")
    if task_id not in JAVA_TASKS: return item

    task = JAVA_TASKS[task_id]
    declaration_head = task["declaration"].replace("class Problem", "class Solution")
    func_name = extract_java_func_name(declaration_head)

    temp_dir = tempfile.mkdtemp()
    runner = JavaExecutionRunner(temp_dir)
    
    snippet_log_path = os.path.join(LOG_ROOT, f"{task_id}.log")
    
    if "class Solution" in item["program"]:
        snippet_code = STANDARD_IMPORTS + "\n" + item["program"]
    else:
        snippet_code = build_java_code(declaration_head, item["program"])        

    if EVAL_MODE == "base":
        success, compile_err = runner.prepare_and_compile_base_split(snippet_code, task["test"])

        if not success:
            with open(snippet_log_path, "w", encoding="utf-8") as f:
                f.write(f"Task: {task_id} | [COMPILE ERROR]\n{compile_err}\n")
            item["pass_ratio"] = 0.0
        else:
            score, all_logs, failures = runner.run_base_split()
            item["pass_ratio"] = score

            if score < 1.0 or failures:
                with open(snippet_log_path, "w", encoding="utf-8") as f:
                    f.write(f"Task: {task_id} | Calculated Rational Score: {score}\n\n")
                    if failures:
                        f.write("\n".join(failures))
                    else:
                        f.write("No failure logs found. (Potential instrumentation failure: __t is 0)")
    else:
        task_inputs = INPUTS_MAP.get(f"Java/{task_id}", [])
        if not task_inputs: return item
        golden_code = build_java_code(declaration_head, task["canonical_solution"])
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
                        f"Index: {idx} | Input: {task_inputs[idx-1]}\nExpected: {gold}\nGot: {pred}\n{'-' * 40}"
                    )
            if failure_details:
                with open(snippet_log_path, "w", encoding="utf-8") as f:
                    f.write(f"Task: {task_id} | Failures: {len(failure_details)}/{len(task_inputs)}\n\n")
                    f.write("\n".join(failure_details))
            item["pass_ratio_evalplus"] = passed / len(task_inputs)

    shutil.rmtree(temp_dir)
    return item

def init_worker(mode, log_path):
    global JAVA_TASKS, INPUTS_MAP, EVAL_MODE, LOG_ROOT
    EVAL_MODE = mode
    LOG_ROOT = log_path

    JAVA_TASKS = {}
    with open(HEX_JAVA_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            data = json.loads(line)
            JAVA_TASKS[data["task_id"].split("/")[-1]] = data
    
    INPUTS_MAP = {}
    with open(HEX_JAVA_INPUT, 'r', encoding='utf-8') as f:
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
    os.path.join(RESULT_ROOT, "java", "gpt-3.5-turbo-1106-1-0-0.0-sample-0.json"),  #small-test set
    # os.path.join(RESULT_ROOT, "java", "gpt-3.5-turbo-1106-2-0-0-0.0-sample-0.json") #small-validation set
]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, choices=["base", "evalplus"], default="base")
    args = parser.parse_args()

    curr_log_root = os.path.join(LOG_ROOT, "java", args.mode)
    if os.path.exists(curr_log_root): 
        shutil.rmtree(curr_log_root)
    os.makedirs(curr_log_root, exist_ok=True)

    total_results = {}

    for grade_file in GRADE_FILES:
        summary_data = process_single_grade_file(grade_file, args.mode, curr_log_root)
        total_results.update(summary_data)

    suffix = "base" if args.mode == "base" else "plus"
    output_path = f"humaneval_java_codejudge_{suffix}_ratio.json"
    output_path = os.path.join(RESULT_ROOT, "java", output_path)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(total_results, f, indent=4)
    
    print(f"Summary of Rational Scores saved to {output_path}")

if __name__ == "__main__":
    main()