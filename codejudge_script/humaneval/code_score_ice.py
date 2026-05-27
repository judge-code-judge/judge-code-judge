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



def read_data(
    test_cases, model, analyze_prompt, compare_prompt, temperature, file_name, overwrite
):
    language = test_cases.split("-")[0]
    with open(f"./data/humaneval/dataset/{language}.json") as f:
        dataset = json.load(f)

    data = []
    with open(f"./data/humaneval/test_cases/{test_cases}.jsonl") as f:
        for line in f:
            data.append(json.loads(line))

    test_name = test_cases.split(".")[0]
    if os.path.exists(f"./output/humaneval/{test_name}/" + file_name) and not overwrite:
        with open(f"./output/humaneval/{test_name}/" + file_name) as f:
            out = json.load(f)
    else:
        if analyze_prompt is not None:
            out = {
                "parameters": {
                    "model": model,
                    "analyze_prompt": analyze_prompt,
                    "compare_prompt": compare_prompt,
                    "temperature": temperature,
                },
                "data": [],
            }
        else:
            out = {
                "parameters": {
                    "model": model,
                    "compare_prompt": compare_prompt,
                    "temperature": temperature,
                },
                "data": [],
            }
    return data, dataset, out


def read_data_icescore(
    test_cases, model, analyze_prompt, compare_prompt, temperature, file_name, overwrite
):
    language = test_cases.split("-")[0]
    with open(f"./data/humaneval/dataset/{language}.json") as f:
        dataset = json.load(f)
    

    data = []

    if "js" in language:
        with open("../data/humaneval-x/humaneval_js_grade_with_execution.json", "r") as f:
            json_data = json.load(f)
    elif "cpp" in language:
        with open("../data/humaneval-x/humaneval_cpp_grade.json", "r") as f:
            json_data = json.load(f)
    elif "java" in language:
        with open("../data/humaneval-x/humaneval_java_grade.json", "r") as f:
            json_data = json.load(f)
    elif "python" in language:
        with open("./data/humaneval/test_cases/humaneval_python_grade.json", "r") as f:
            json_data = json.load(f)  # 전체가 list 구조
    elif "go" in language:
        with open("../data/humaneval-x/humaneval_go_grade_body_only.json", "r") as f:
            json_data = json.load(f)
    else:
        with open("./data/humaneval/test_cases/humaneval_python_grade.json", "r") as f:
            json_data = json.load(f)  # 전체가 list 구조



    for item in json_data:
        question_id = item.get("task_id")

        # 숫자로만 이루어진 key들만 추출 → program dict 구성
        program_dict = {}
        for key, value in item.items():
            if key.isdigit():
                program_dict[key] = value

        data.append({
            "question_id": question_id,
            "program": program_dict
        })

    test_name = test_cases.split(".")[0]
    if os.path.exists(f"./output/humaneval/{test_name}/" + file_name) and not overwrite:
        with open(f"./output/humaneval/{test_name}/" + file_name) as f:
            out = json.load(f)
    else:
        if analyze_prompt is not None:
            out = {
                "parameters": {
                    "model": model,
                    "analyze_prompt": analyze_prompt,
                    "compare_prompt": compare_prompt,
                    "temperature": temperature,
                },
                "data": [],
            }
        else:
            out = {
                "parameters": {
                    "model": model,
                    "compare_prompt": compare_prompt,
                    "temperature": temperature,
                },
                "data": [],
            }
    return data, dataset, out


def get_pair(item, dataset, with_prefix, testcase):
    question_id = item["question_id"]
    if with_prefix or True:
        if "go" in testcase:
            canonical_solution = (
                dataset[question_id]["prompt"]
                + dataset[question_id]["canonical_solution"]
            )
        else:
            canonical_solution = (
                dataset[question_id]["declaration"]
                + dataset[question_id]["canonical_solution"]
            )
        # print(item["program"])

        # print(dataset[question_id]["declaration"].replace('"', ''))
        for program_id, program in item["program"].items():
            if "go" in testcase:
                item["program"][program_id] = program
                continue
            if "java" in testcase or "cpp" in testcase:
                item["program"][program_id] = dataset[question_id]["declaration"].replace('"', '') + "    " + program + "\n}"
                # print(item["program"][program_id])
                continue
            item["program"][program_id] = dataset[question_id]["declaration"].replace('"', '') + "    " + program 
            
        
    else:
        canonical_solution = dataset[question_id]["canonical_solution"]
        program = item["program"]
        # Some programs are empty
        if program == "":
            program = "<empty>"

    return item["program"], canonical_solution


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
    data, dataset, out = read_data_icescore(
        test_case, model, None, compare_prompt, temperature, file_name, overwrite
    )

    with open("./data/humaneval/nl.json", "r") as f:
        nl = json.load(f)
    with open("./data/humaneval/example.json", "r") as f:
        example = json.load(f)

    if overwrite:
        start_index = 0
    else:
        if len(out["data"]) == len(data):
            return
        start_index = len(out["data"])

    snippet_count = 0

    terminators, pipeline = load_model(model)
    for item in tqdm(data[start_index:]):
        # print(item)
        programs, canonical_solution = get_pair(item, dataset, with_prefix, test_case)

        for program_id, program in programs.items():
            # print("Program")
            # print(program)
            # print("Canonical")
            # print(canonical_solution)
            code_gpt_answer = form_filling(
                model,
                compare_prompt,
                terminators,
                pipeline,
                temperature,
                info={
                    "CODE1": program,
                    "CODE2": canonical_solution,
                    "PROBLEM": nl[item["question_id"]],
                    "EXAMPLE": example[item["question_id"]],
                    "LANGUAGE": test_case.split("-")[0],
                },
            )
            # print(code_gpt_answer)
            code_gpt_score = answer_to_score(code_gpt_answer, return_type)
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

            snippet_count += 1

            # if snippet_count > 10:
            #     break

        test_name = test_case.split(".")[0]
        directory_path = f"./output/humaneval-x/{test_name}/ice_data_check/"
        os.makedirs(directory_path, exist_ok=True)
        print(directory_path + file_name)

        with open(directory_path + file_name, "w") as f:
            json.dump(out, f, indent=4)
        # if snippet_count > 10:
        #     break


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
    data, dataset, out = read_data_icescore(
        test_case,
        model,
        analyze_prompt,
        compare_prompt,
        temperature,
        file_name,
        overwrite,
    )

    with open("./data/humaneval/nl.json", "r") as f:
        nl = json.load(f)
    with open("./data/humaneval/example.json", "r") as f:
        example = json.load(f)

    # if overwrite:
    #     start_index = 0
    # else:
    #     if len(out["data"]) == len(data):
    #         return
    #     start_index = len(out["data"])

    start_index = 0

    terminators, pipeline = load_model(model)
    snippet_count = 0

    for item in tqdm(data[start_index:]):
        programs, canonical_solution = get_pair(item, dataset, with_prefix, test_case)

        # print("Program:")
        # print(programs[0])
        # print("Solution:")
        # print(canonical_solution)

        # program = "func HasCloseElements(numbers []float64, threshold float64) bool {\n    for i := 0; i < len(numbers)-1; i++ {\n        if math.Abs(numbers[i] - numbers[i+1]) < threshold {\n            return true\n        }\n    }\n    return false\n}"
        for program_id, program in programs.items():

            print(f'Question: {item["question_id"]}')
            print(program_id)
            # print("Program")
            # print(program)
            # print("Canonical")
            # print(canonical_solution)
            nl_mistakes = form_filling(
                model,
                compare_prompt,
                terminators,
                pipeline,
                temperature,
                info={
                    "CODE1": program,
                    "CODE2": canonical_solution,
                    "PROBLEM": nl[item["question_id"]],
                    "EXAMPLE": example[item["question_id"]],
                    "LANGUAGE": test_case.split("-")[0],
                },
            )
            # print(nl_mistakes)

            code_gpt_answer = form_filling(
                model,
                analyze_prompt,
                terminators,
                pipeline,
                temperature,
                info={
                    "MISTAKES": nl_mistakes,
                    "PROBLEM": nl[item["question_id"]],
                    "EXAMPLE": example[item["question_id"]],
                },
                max_tokens=512,
            )
            print("-----------------------------------------------------------------------")
            # print(code_gpt_answer)
            # print("-----------------------------------------------------------------------")

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
            print("-----------------------------------------------------------------------")
            print(code_gpt_score)

            snippet_count += 1

            # if snippet_count > 10:
            #     break


        test_name = test_case.split(".")[0]
        directory_path = f"./output/humaneval-x/{test_name}/ice_data_check/"
        print(directory_path + file_name)
        os.makedirs(directory_path, exist_ok=True)
        with open(directory_path + file_name, "w") as f:
            json.dump(out, f, indent=4)

        # if snippet_count > 10:
        #     break

        # break


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
        full_file_name = f"{file_name}-sample-{index}_icedata.json"
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
