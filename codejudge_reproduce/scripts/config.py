import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HUMANEVAL_X_PATH = os.path.join(PROJECT_ROOT, "data", "humaneval-x")

HEX_JAVA_PATH = os.path.join(HUMANEVAL_X_PATH, "java.jsonl")
HEX_JAVA_INPUT = os.path.join(HUMANEVAL_X_PATH, "java_inputs.jsonl")
HEX_CPP_PATH = os.path.join(HUMANEVAL_X_PATH, "cpp.jsonl")
HEX_CPP_INPUT = os.path.join(HUMANEVAL_X_PATH, "cpp_inputs.jsonl")
HEX_GO_PATH = os.path.join(HUMANEVAL_X_PATH, "go.jsonl")
HEX_GO_INPUT = os.path.join(HUMANEVAL_X_PATH, "go_inputs.jsonl")

RESULT_ROOT = os.path.join(PROJECT_ROOT, "codejudge_reproduce", "results")

LOG_ROOT = os.path.join(PROJECT_ROOT, "codejudge_reproduce", "logs")
# print(f"PROJECT_ROOT: {PROJECT_ROOT}")
# print(f"HUMANEVAL_X_PATH: {HUMANEVAL_X_PATH}")
# print(f"HEX_JAVA_PATH: {HEX_JAVA_PATH}")
# print(f"HEX_CPP_PATH: {HEX_CPP_PATH}")
# print(f"HEX_GO_PATH: {HEX_GO_PATH}")