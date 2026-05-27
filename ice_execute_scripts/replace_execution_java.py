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

def parse_base_test_cases_java(test_code: str):
    # 1. Main 메서드 본문 추출
    main_match = re.search(r'public\s+static\s+void\s+main\s*\(String\[\]\s+args\)\s*\{', test_code)
    if not main_match: return "", "", [], ""
    
    header = test_code[:main_match.start()].strip()
    body = test_code[main_match.end():].strip()
    if body.endswith('}'): body = body[:-1].strip()
    if body.endswith('}'): body = body[:-1].strip()
    list_match = re.search(r'Arrays\.asList\s*\(', body)
    static_cases = []
    setup_code = body
    remaining_body = ""

    if list_match:
        setup_code = body[:list_match.start()].strip()
        setup_code = re.sub(r'List<Boolean>\s+\w+\s*=\s*$', '', setup_code).strip()
        content_start = list_match.end()
        depth = 1
        curr = content_start
        while curr < len(body) and depth > 0:
            if body[curr] == '"':
                curr += 1
                while curr < len(body) and body[curr] != '"': curr += 1
            elif body[curr] == '(': depth += 1
            elif body[curr] == ')': depth -= 1
            curr += 1
        
        list_content = body[content_start:curr-1]
        static_cases = get_balanced_expressions(list_content)
        remaining_body = body[curr:].strip()
        
        remaining_body = re.sub(r'if\s*\(.*?.contains\(false\)\)\s*\{\s*throw\s+new\s+AssertionError\(.*?\)\s*;\s*\}', '', remaining_body, flags=re.DOTALL)

    return header, setup_code, static_cases, remaining_body

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
    """

class JavaExecutionRunner:
    def __init__(self, work_dir):
        self.work_dir = work_dir
    
    def prepare_and_compile_base_split(self, snippet_code, test_code):
        # 1. Main 메서드 본문 및 Header 추출
        main_match = re.search(r'public\s+static\s+void\s+main\s*\(String\[\]\s+args\)\s*\{', test_code)
        if not main_match:
            return False, "Main method not found"
        
        header = test_code[:main_match.start()].strip()
        body = test_code[main_match.end():].strip()
        if body.endswith('}'): body = body[:-1].strip()
        if body.endswith('}'): body = body[:-1].strip()

        combined_extra = snippet_code + "\n" + header
        all_imports = set(re.findall(r'^import\s+[\w\.]+.*;', combined_extra, re.MULTILINE))
        all_imports.update([
            "import java.util.*;", "import java.math.*;", "import java.io.*;",
            "import java.util.stream.*;", "import java.util.function.*;"
        ])

        clean_snippet = re.sub(r'^import\s+[\w\.]+.*;', '', snippet_code, flags=re.MULTILINE)
        clean_header = re.sub(r'^import\s+[\w\.]+.*;', '', header, flags=re.MULTILINE)
        # header에서 'public class Main {' 잔재 제거
        clean_header = re.sub(r'public\s+class\s+Main\s*\{', '', clean_header).strip()

        list_match = re.search(r'Arrays\.asList\s*\(', body)
        setup_code = body
        static_exprs = []
        transformed_body = ""

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
            r'if\s*\(\s*!(.*?)\)\s*\{\s*throw\s+new\s+AssertionError\(.*?\)\s*;\s*\}', 
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
{clean_header}
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


def evaluate_single_task_java(item):
    global JAVA_TASKS, INPUTS_MAP, EVAL_MODE, LOG_ROOT
    task_id = item["task_id"]
    if task_id not in JAVA_TASKS: return item

    task = JAVA_TASKS[task_id]
    task_inputs = INPUTS_MAP.get(f"Java/{task_id}", [])
    if not task_inputs: return item

    declaration_head = task["declaration"].replace("class Problem", "class Solution")
    func_name = extract_java_func_name(declaration_head)

    temp_dir = tempfile.mkdtemp()
    runner = JavaExecutionRunner(temp_dir)
    
    golden_code = build_java_code(declaration_head, task["canonical_solution"])
    runner.prepare_and_compile(golden_code, func_name, task_inputs)
    golden_results = runner.run_all(len(task_inputs))

    log_dir = os.path.join(LOG_ROOT, task_id)
    os.makedirs(log_dir, exist_ok=True)

    for key in list(item.keys()):
        if not key.isdigit(): continue
        
        if f"grade-{key}" not in item:
            item[f"grade-{key}"] = {}

        snippet_log_path = os.path.join(LOG_ROOT, task_id, f"{key}.log")             
        snippet_code = build_java_code(declaration_head, item[key])

        if EVAL_MODE == "base":
            success, compile_err = runner.prepare_and_compile_base_split(snippet_code, task["test"])

            if not success:
                with open(snippet_log_path, "w", encoding="utf-8") as f:
                    f.write(f"Snippet: {key} | [COMPILE ERROR]\n{compile_err}\n")
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
                        f"Index: {idx} | Input: {task_inputs[idx-1]}\nExpected: {gold}\nGot: {pred}\n{'-' * 40}"
                    )

            if failure_details:
                with open(snippet_log_path, "w", encoding="utf-8") as f:
                    f.write(f"Snippet: {key} | Failures: {len(failure_details)}/{len(task_inputs)}\n\n")
                    f.write("\n".join(failure_details))

            item[f"grade-{key}"]["execution_custom"] = passed / len(task_inputs)

    shutil.rmtree(temp_dir)
    return item

def init_worker(mode, log_path):
    global JAVA_TASKS, INPUTS_MAP, EVAL_MODE, LOG_ROOT
    EVAL_MODE = mode
    LOG_ROOT = log_path

    JAVA_TASKS = {}
    with open(JAVA_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            data = json.loads(line)
            JAVA_TASKS[data["task_id"].split("/")[-1]] = data
    
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


JAVA_FILE = "data/humaneval-x/java.jsonl"             
INPUT_JSONL = "data/humaneval-x/java_inputs.jsonl"

GRADE_FILE = "data/humaneval-x/humaneval_java_grade_evalplus_ratio.json"
OUTPUT_JSON = "data/humaneval-x/humaneval_java_grade_evalplus_ratio.json" 

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, choices=["base", "custom"], default="base")
    args = parser.parse_args()

    curr_log_root = f"results/java_eval_{args.mode}"

    if os.path.exists(curr_log_root):
        print(f"Cleaning up old logs in {curr_log_root}...")
        shutil.rmtree(curr_log_root)
    os.makedirs(curr_log_root, exist_ok=True)

    if not os.path.exists(GRADE_FILE):
        print(f"Error: {GRADE_FILE} not Exists")
        return
    
    if GRADE_FILE != OUTPUT_JSON:
        shutil.copy2(GRADE_FILE, OUTPUT_JSON)

    with open(OUTPUT_JSON, "r") as f:
        data = json.load(f)

    print(f"Starting Java Execution Evaluation | Workers: {MAX_PARALLEL_TASKS}")
    
    with mp.Pool(processes=MAX_PARALLEL_TASKS, initializer=init_worker, initargs=(args.mode, curr_log_root)) as pool:
        for updated_task in tqdm(pool.imap_unordered(evaluate_single_task_java, data), total=len(data)):
            if updated_task:
                for i in range(len(data)):
                    if data[i]["task_id"] == updated_task["task_id"]:
                        data[i] = updated_task
                        break
                atomic_save_json(data, OUTPUT_JSON)

    print(f"Finished! Updated results saved to {OUTPUT_JSON}")

if __name__ == "__main__":
    main()