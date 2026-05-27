import pandas as pd
import numpy as np
import argparse
import os
import time
import utils

METRICS = [
    "ICE-Score", "ICE-Score w/o r", 
    "CJ-BIN", "CJ-BIN w/o r", "CJ-Score", "CJ-Score w/o r", 
    "V-BIN", "V-BIN w/o r", "V-Score", "V-Score w/o r"
]

def generate_latex(res_gpt, res_qwen, n_bootstraps, use_sig=False, is_original=False):
    if is_original:
        caption_lines = [
            r"    \caption{The original per-language correlation results without bootstrap (GPT-5-nano vs Qwen3-Next-80B).}"
        ]
    else:
        caption_lines = [
            r"    \caption{The per-language results of E2 (see \S~\ref{sec:more-recent-llms}).",
            r"        We present the results in the format of X / Y, where X corresponds to the results with \gptfivenano and Y to the results with \qwenthree.",
            r"    }"
        ]
        
    lines = [
        r"\begin{table*}[!t]",
        r"    \centering",
        r"    \scriptsize",
        r"    \setlength{\tabcolsep}{0.8pt}"
    ]
    lines.extend(caption_lines)
    lines.extend([
        r"    \label{tab:per-language}",
        r"    \begin{tabular}{@{}c|c|ccc|ccc|ccc@{}}",
        r"        \toprule",
        r"                    & Method                  & $r$             & $r_b$           & $r-r_b$                                     & $\rho$          & $\rho_b$        & $\rho-\rho_b$                               & $\tau$          & $\tau_b$        & $\tau-\tau_b$                               \\",
        r"        \midrule",
        r"        \midrule"
    ])

    # per-language.tex 양식에 맞춘 정확한 고정 폭 패딩 값
    W_METH = 24
    W_VAL  = 16
    W_DIFF = 44

    for i, lang in enumerate(utils.LANGUAGES):
        display_name = utils.LANG_DISPLAY_MAP[lang]
        
        valid_metric_count = len([m for m in METRICS if m in res_gpt.get(lang, {}) or m in res_qwen.get(lang, {})])
        
        # 첫 언어가 아닌 경우 언어 블록 사이의 \midrule 추가
        if i > 0:
            lines.extend([r"        \midrule", r"        \midrule"])
            
        # multirow를 별도의 라인으로 분리 (원본 양식 100% 동일 구현)
        lines.append(f"        \\multirow{{{valid_metric_count}}}{{*}}{{\\rotatebox{{90}}{{{display_name}}}}}    ")

        for metric in METRICS:
            gpt_lang_res = res_gpt.get(lang, {})
            qwen_lang_res = res_qwen.get(lang, {})
            
            if metric not in gpt_lang_res and metric not in qwen_lang_res: 
                continue
                
            method_name = utils.LATEX_METHOD_MAP.get(metric, metric)
            method_padded = f"{method_name:<{W_METH}}"
            
            cols = []
            for corr_type in ["pearson", "spearman", "kendall"]:
                gpt_data = gpt_lang_res.get(metric, {}).get(corr_type, {"r": np.nan, "b": np.nan, "diff": np.nan, "sig": 0})
                qwen_data = qwen_lang_res.get(metric, {}).get(corr_type, {"r": np.nan, "b": np.nan, "diff": np.nan, "sig": 0})
                
                s_r = f"{utils.fmt(gpt_data['r'])} / {utils.fmt(qwen_data['r'])}"
                s_b = f"{utils.fmt(gpt_data['b'])} / {utils.fmt(qwen_data['b'])}"
                
                if use_sig:
                    str_gpt = utils.format_sig_str(gpt_data['diff'], gpt_data['sig'])
                    str_qwen = utils.format_sig_str(qwen_data['diff'], qwen_data['sig'])
                else:
                    str_gpt = utils.format_plain_str(gpt_data['diff'])
                    str_qwen = utils.format_plain_str(qwen_data['diff'])
                    
                s_diff = f"{str_gpt} / {str_qwen}"
                
                # 수치와 델타값을 지정된 길이에 맞춰 좌측 정렬(ljust) 공백 채우기
                cols.extend([f"{s_r:<{W_VAL}}", f"{s_b:<{W_VAL}}", f"{s_diff:<{W_DIFF}}"])
            
            # 정확히 20칸 들여쓰기 후 데이터 결합
            row_str = f"                    & {method_padded} & " + " & ".join(cols) + r" \\"
            lines.append(row_str)
            
            # 블록 내부의 서브 경계선 추가
            if metric in ["ICE-Score w/o r", "CJ-Score w/o r"]:
                lines.append(r"        \cmidrule{2-11}")

    lines.extend([
        r"        \bottomrule",
        r"    \end{tabular}",
        r"\end{table*}"
    ])
    return "\n".join(lines)

OUTPUT_DIR = os.path.join(utils.HERE, "../table_results/table_3")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n_bootstraps", type=int, default=10000)
    args = parser.parse_args()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print("[INFO] Loading and filtering data for GPT-5-nano vs Qwen3-Next-80B (Per Language)...")
    
    df_plus = pd.read_csv(utils.X_PLUS_DATA) if utils.X_PLUS_DATA.endswith('.csv') else pd.read_parquet(utils.X_PLUS_DATA)
    
    df_gpt = df_plus[df_plus["LLM_MODEL"] == "GPT-5-nano"].copy()
    df_qwen = df_plus[df_plus["LLM_MODEL"] == "Qwen3-Next-80B"].copy()
    
    print("\n[PHASE 1] Calculating ORIGINAL correlations (No Bootstrap)...")
    
    res_gpt, res_qwen = {}, {}
    for lang in utils.LANGUAGES:
        df_gpt_lang = df_gpt[df_gpt["LANG"] == lang]
        df_qwen_lang = df_qwen[df_qwen["LANG"] == lang]
        
        res_gpt[lang] = utils.get_original_correlations(df_gpt_lang, "EXECUTION_PLUS_BINARY", "EXECUTION_PLUS_RATIO", METRICS)
        res_qwen[lang] = utils.get_original_correlations(df_qwen_lang, "EXECUTION_PLUS_BINARY", "EXECUTION_PLUS_RATIO", METRICS)
    
    tex_original = generate_latex(res_gpt, res_qwen, n_bootstraps=0, use_sig=False, is_original=True)
    original_path = os.path.join(OUTPUT_DIR, "correlation_table_original.tex")
    
    with open(original_path, "w", encoding="utf-8") as f:
        f.write(tex_original)
    print(f"Saved Original Correlations to: {original_path}")

    print(f"\n[PHASE 2] Starting Bootstraps ({args.n_bootstraps} iterations).")
    start_time = time.time()
    
    for lang in utils.LANGUAGES:
        df_gpt_lang = df_gpt[df_gpt["LANG"] == lang]
        df_qwen_lang = df_qwen[df_qwen["LANG"] == lang]
        
        utils.apply_bootstraps(df_gpt_lang, "EXECUTION_PLUS_BINARY", "EXECUTION_PLUS_RATIO", res_gpt[lang], METRICS, args.n_bootstraps, pbar_desc=f"GPT-5-nano [{lang}]")
        utils.apply_bootstraps(df_qwen_lang, "EXECUTION_PLUS_BINARY", "EXECUTION_PLUS_RATIO", res_qwen[lang], METRICS, args.n_bootstraps, pbar_desc=f"Qwen3 [{lang}]")
    
    print(f"Bootstrap elapsed time: {time.time() - start_time:.2f} seconds")

    tex_sig = generate_latex(res_gpt, res_qwen, args.n_bootstraps, use_sig=True, is_original=False)
    with open(os.path.join(OUTPUT_DIR, "correlation_table_sig.tex"), "w", encoding="utf-8") as f:
        f.write(tex_sig)
        
    print("\nAll tasks completed successfully! Final tables saved.")