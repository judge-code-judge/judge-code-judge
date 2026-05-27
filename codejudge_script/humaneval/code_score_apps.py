import os
import json
import sys
import argparse

sys.path.append(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
from code_model_score import form_filling, answer_to_score, load_model
from prompts import single_step_prompt, dual_step_prompt

import torch
from tqdm import tqdm

from datasets import load_dataset

APPS_TEST_PATH = "../data/APPS/test"


def load_apps_reference(task_id):
    task_id_padded = str(task_id).zfill(4)
    path = os.path.join(APPS_TEST_PATH, task_id_padded, "solutions.json")
    # print(path)

    if not os.path.exists(path):
        return None

    try:
        with open(path, "r") as f:
            sols = json.load(f)
        return sols[0] if len(sols) > 0 else None
    except:
        return None


def read_data_codejudge(model, analyze_prompt, compare_prompt, temperature, file_name, overwrite):

    ds = load_dataset("CodeResearch/CodeJudge-Eval")["train"]

    # 🔥 task_id 기준 groupby
    from collections import defaultdict
    grouped = defaultdict(list)

    for item in ds:
        grouped[item["task_id"]].append(item)

    data = []

    for task_id, items in grouped.items():
        program_dict = {}

        for item in items:
            program_dict[str(item["data_id"])] = item["code"]

        data.append({
            "question_id": str(task_id),
            "program": program_dict,
            "statement": items[0]["statement"]
        })

    # output 구조
    if os.path.exists(file_name) and not overwrite:
        with open(file_name) as f:
            out = json.load(f)
    else:
        out = {
            "parameters": {
                "model": model,
                "analyze_prompt": analyze_prompt,
                "compare_prompt": compare_prompt,
                "temperature": temperature,
            },
            "data": [],
        }

    return data, out

def get_pair_codejudge(item, with_prefix):
    task_id = item["question_id"]

    canonical_solution = load_apps_reference(task_id)

    programs = item["program"]
    # print(task_id)

    # prefix 없음 (HumanEval과 달리 function signature 없음)
    return programs, canonical_solution

def single_step_workflow(
    test_case,
    model,
    compare_prompt,
    temperature,
    file_name,
    with_prefix,
    return_type,
    overwrite,
):
    data, out = read_data_codejudge(
        model, None, compare_prompt, temperature, file_name, overwrite
    )

    if overwrite:
        start_index = 0
    else:
        start_index = len(out["data"])

    terminators, pipeline = load_model(model)

    for item in tqdm(data[start_index:]):
        programs, canonical_solution = get_pair_codejudge(item, with_prefix)

        for program_id, program in programs.items():

            code_gpt_answer = form_filling(
                model,
                compare_prompt,
                terminators,
                pipeline,
                temperature,
                info={
                    "CODE1": program,
                    "CODE2": canonical_solution,
                    "PROBLEM": item["statement"],   # 🔥 핵심 변경
                    "EXAMPLE": "",                  # 없음
                    "LANGUAGE": "python",
                },
            )

            code_gpt_score = answer_to_score(code_gpt_answer, return_type)
            print(code_gpt_answer)
            print(code_gpt_score)

            new_result = {
                "program_id": program_id,
                "program": program,
                "canonical_solution": canonical_solution,
                "code_gpt_score": {
                    "code_gpt_score": float(code_gpt_score),
                    "comparison": code_gpt_answer,
                },
                "question_id": item["question_id"],
            }

            out["data"].append(new_result)

        os.makedirs("./output/codejudge_check/", exist_ok=True)
        print(f"./output/codejudge_check/" + file_name)

        with open(f"./output/codejudge_check/" + file_name, "w") as f:
            json.dump(out, f, indent=4)


def dual_step_workflow(
    test_case,
    model,
    analyze_prompt,
    compare_prompt,
    temperature,
    file_name,
    with_prefix,
    return_type,
    overwrite,
):
    import os
    import json
    from tqdm import tqdm

    # 🔥 dedup 포함 데이터 로드
    data, out = read_data_codejudge(
        model,
        analyze_prompt,
        compare_prompt,
        temperature,
        file_name,
        overwrite,
    )

    # overwrite 여부
    if overwrite:
        start_index = 0
    else:
        start_index = len(out["data"])

    terminators, pipeline = load_model(model)

    task_count = 0

    for item in tqdm(data[start_index:]):


        programs, canonical_solution = get_pair_codejudge(item, with_prefix)

        for program_id, program in programs.items():

            # -------------------------
            # Step 1: 비교 (mistake 추출)
            # -------------------------
            nl_mistakes = form_filling(
                model,
                compare_prompt,
                terminators,
                pipeline,
                temperature,
                info={
                    "CODE1": program,
                    "CODE2": canonical_solution if canonical_solution else "",
                    "PROBLEM": item["statement"],
                    "EXAMPLE": "",          # CodeJudge에는 없음
                    "LANGUAGE": "python",
                },
            )

            # -------------------------
            # Step 2: 분석 → score
            # -------------------------
            code_gpt_answer = form_filling(
                model,
                analyze_prompt,
                terminators,
                pipeline,
                temperature,
                info={
                    "MISTAKES": nl_mistakes,
                    "PROBLEM": item["statement"],
                    "EXAMPLE": "",
                },
                max_tokens=512,
            )

            code_gpt_score = answer_to_score(code_gpt_answer, return_type)

            new_result = {
                "program_id": program_id,
                "program": program,
                "canonical_solution": canonical_solution,
                "code_gpt_score": {
                    "code_gpt_score": float(code_gpt_score),
                    "comparison": nl_mistakes,
                    "parsed_comparison": code_gpt_answer,
                },
                "question_id": item["question_id"],
            }

            out["data"].append(new_result)

            print(code_gpt_score)

        # 🔴 매 task마다 저장 (중간 저장 중요)
        os.makedirs("./output/codejudge_check/", exist_ok=True)
        print(f"./output/codejudge_check/" + file_name)

        with open(f"./output/codejudge_check/" + file_name, "w") as f:
            json.dump(out, f, indent=4)

        # if task_count > 10:
        #     print("Finish!")
        #     break
        # else:
        #     task_count += 1
        


def router(
    test_case,
    model,
    step,
    temperature,
    with_prefix,
    return_type,
    num_samples,
    overwrite,
    analyze_prompt=None,
    compare_prompt=None,
    file_name=None,
):
    for index in range(num_samples):
        full_file_name = f"{file_name}-sample-{index}_apps.json"
        if step == 1:
            print(full_file_name)
            single_step_workflow(
                test_case,
                model,
                compare_prompt,
                temperature,
                full_file_name,
                with_prefix,
                return_type,
                overwrite,
            )
        elif step == 2:
            print(full_file_name)
            dual_step_workflow(
                test_case,
                model,
                analyze_prompt,
                compare_prompt,
                temperature,
                full_file_name,
                with_prefix,
                return_type,
                overwrite,
            )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--test_case", type=str)
    parser.add_argument("--model", type=str, default="gpt-3.5-turbo")
    parser.add_argument("--step", type=int, default=1)
    parser.add_argument("--analyze_prompt", type=int, default=0)
    parser.add_argument("--compare_prompt", type=int, default=0)
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--with_prefix", action="store_true")
    parser.add_argument("--return_type", type=str, default="bool")
    parser.add_argument("--num_samples", type=int, default=1)
    parser.add_argument("--overwrite", action="store_true")

    args = parser.parse_args()

    test_case = args.test_case
    model = args.model
    step = args.step
    analyze_prompt_index = args.analyze_prompt
    compare_prompt_index = args.compare_prompt
    temperature = args.temperature
    with_prefix = args.with_prefix
    return_type = args.return_type
    num_samples = args.num_samples
    overwrite = args.overwrite

    if step == 1:
        analyze_prompt = None
        compare_prompt = single_step_prompt[compare_prompt_index]
        if not with_prefix:
            file_name = f"{model}-1-{compare_prompt_index}-{temperature}-without-prefix"
        else:
            file_name = (
                f"{model}-1-{compare_prompt_index}-{temperature}"
            )
    elif step == 2:
        analyze_prompt = dual_step_prompt["analyze_prompt"][analyze_prompt_index]
        compare_prompt = dual_step_prompt["compare_prompt"][compare_prompt_index]
        if not with_prefix:
            file_name = f"{model}-2-{analyze_prompt_index}-{compare_prompt_index}-{temperature}-without-prefix"
        else:
            file_name = f"{model}-2-{analyze_prompt_index}-{compare_prompt_index}-{temperature}"

    router(
        test_case,
        model,
        step,
        temperature,
        with_prefix,
        return_type,
        num_samples,
        overwrite,
        analyze_prompt=analyze_prompt,
        compare_prompt=compare_prompt,
        file_name=file_name,
    )


if __name__ == "__main__":
    main()
