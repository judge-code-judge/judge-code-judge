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
    caption = "The original correlation results without bootstrap (GPT-5-nano vs Qwen3-Next-80B)." if is_original else f"The results of unified correlation evaluation with {n_bootstraps} bootstraps (GPT-5-nano vs Qwen3-Next-80B)."
    
    lines = [
        r"\begin{table*}[t]", r"    \centering", r"    \scriptsize", r"    \setlength{\tabcolsep}{3.5pt}",
        f"    \\caption{{{caption}}}",
        r"    \label{tab:unified-settings-models}", r"    \begin{tabular}{@{}", r"        l|ccc|ccc|ccc@{}}", r"        \toprule",
        r"        Method & $r$ & $r_b$ & $r-r_b$ & $\rho$ & $\rho_b$ & $\rho-\rho_b$ & $\tau$ & $\tau_b$ & $\tau-\tau_b$ \\",
        r"        \midrule", r"        \midrule"
    ]
    
    for metric in METRICS:
        if metric not in res_gpt and metric not in res_qwen: continue
            
        row_str = f"        {utils.LATEX_METHOD_MAP.get(metric, metric):<23} & "
        cols = []
        
        for corr_type in ["pearson", "spearman", "kendall"]:
            gpt_data = res_gpt.get(metric, {}).get(corr_type, {"r": np.nan, "b": np.nan, "diff": np.nan, "sig": 0})
            qwen_data = res_qwen.get(metric, {}).get(corr_type, {"r": np.nan, "b": np.nan, "diff": np.nan, "sig": 0})
            
            s_r = f"{utils.fmt(gpt_data['r'])} / {utils.fmt(qwen_data['r'])}" if pd.notna(gpt_data['r']) or pd.notna(qwen_data['r']) else ""
            s_b = f"{utils.fmt(gpt_data['b'])} / {utils.fmt(qwen_data['b'])}" if pd.notna(gpt_data['b']) or pd.notna(qwen_data['b']) else ""
            
            if use_sig:
                str_gpt = utils.format_sig_str(gpt_data['diff'], gpt_data['sig'])
                str_qwen = utils.format_sig_str(qwen_data['diff'], qwen_data['sig'])
            else:
                str_gpt = utils.format_plain_str(gpt_data['diff'])
                str_qwen = utils.format_plain_str(qwen_data['diff'])
                
            s_diff = ""
            if str_gpt or str_qwen:
                s_diff = f"{str_gpt} / {str_qwen}" if str_gpt and str_qwen else f"{str_gpt} / " if str_gpt else f" / {str_qwen}"
            
            cols.extend([f"{s_r:<15}", f"{s_b:<15}", f"{s_diff:<35}"])
            
        row_str += " & ".join(cols) + r" \\"
        lines.append(row_str)
        
        if metric in ["ICE-Score w/o r", "CJ-Score w/o r"]:
            lines.append(r"        \midrule")

    lines.extend([
        r"        \bottomrule", r"    \end{tabular}\\", r"    \begin{description}[itemsep=-3pt,topsep=0pt]",
        r"        \item \textbf{CJ:} CodeJudge. \hspace{10pt} \textbf{w/o r}: without reference.",
        r"        \item \textbf{GPT-5-nano / Qwen3-Next-80B format}. $r$: ratio correlation, $r_b$: binary correlation.",
    ])
    
    if use_sig:
        lines.append(r"        \item \textbf{Note}: Bold text indicates significance at $p < 0.05$. Highlighted bold text indicates significance at $p < 0.01$.")
        
    lines.extend([r"    \end{description}", r"\end{table*}"])
    return "\n".join(lines)

OUTPUT_DIR = os.path.join(utils.HERE, "../table_results/table_2")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n_bootstraps", type=int, default=10000)
    args = parser.parse_args()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print("[INFO] Loading and filtering data for GPT-5-nano vs Qwen3-Next-80B...")
    df_plus = pd.read_csv(utils.X_PLUS_DATA) if utils.X_PLUS_DATA.endswith('.csv') else pd.read_parquet(utils.X_PLUS_DATA)
    
    df_gpt = df_plus[df_plus["LLM_MODEL"] == "GPT-5-nano"].copy()
    df_qwen = df_plus[df_plus["LLM_MODEL"] == "Qwen3-Next-80B"].copy()
    
    if df_gpt.empty or df_qwen.empty:
        print("[WARNING] 한쪽 모델의 데이터가 비어있습니다. Parquet 파일 내 모델명을 다시 한 번 확인해주세요.")
    
    print("\n[PHASE 1] Calculating ORIGINAL correlations (No Bootstrap)...")
    res_gpt = utils.get_original_correlations(df_gpt, "EXECUTION_PLUS_BINARY", "EXECUTION_PLUS_RATIO", METRICS)
    res_qwen = utils.get_original_correlations(df_qwen, "EXECUTION_PLUS_BINARY", "EXECUTION_PLUS_RATIO", METRICS)
    
    tex_original = generate_latex(res_gpt, res_qwen, n_bootstraps=0, use_sig=False, is_original=True)
    original_path = os.path.join(OUTPUT_DIR, "correlation_table_original.tex")
    
    with open(original_path, "w", encoding="utf-8") as f:
        f.write(tex_original)
    print(f"Saved Original Correlations to: {original_path}")

    print(f"\n[PHASE 2] Starting Bootstraps ({args.n_bootstraps} iterations)")
    start_time = time.time()
    
    res_gpt = utils.apply_bootstraps(df_gpt, "EXECUTION_PLUS_BINARY", "EXECUTION_PLUS_RATIO", res_gpt, METRICS, args.n_bootstraps, pbar_desc="GPT-5-nano")
    print(f"  - GPT-5-nano data bootstraps completed.")
    
    res_qwen = utils.apply_bootstraps(df_qwen, "EXECUTION_PLUS_BINARY", "EXECUTION_PLUS_RATIO", res_qwen, METRICS, args.n_bootstraps, pbar_desc="Qwen3-Next")
    print(f"  - Qwen3-Next-80B data bootstraps completed.")
    
    print(f"Bootstrap elapsed time: {time.time() - start_time:.2f} seconds")

    tex_sig = generate_latex(res_gpt, res_qwen, args.n_bootstraps, use_sig=True, is_original=False)
    with open(os.path.join(OUTPUT_DIR, "correlation_table_sig.tex"), "w", encoding="utf-8") as f:
        f.write(tex_sig)
        
    print("\nAll tasks completed successfully! Final tables saved.")