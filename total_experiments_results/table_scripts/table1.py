import pandas as pd
import numpy as np
import argparse
import os
import time
import utils

METRICS = [
    "BLEU", "ROUGE-L", "METEOR", "chrF", "CodeBLEU", "RUBY", 
    "CBS_F1", "CBS_F3", "ICE-Score", "ICE-Score w/o r", 
    "CJ-BIN", "CJ-BIN w/o r", "CJ-Score", "CJ-Score w/o r", 
    "V-BIN", "V-BIN w/o r", "V-Score", "V-Score w/o r"
]

def generate_latex(res_base, res_plus, n_bootstraps, use_sig=False, custom_caption=None):
    if custom_caption:
        caption = custom_caption
    else:
        caption = f"The results of unified correlation evaluation with {n_bootstraps} bootstraps."
    
    lines = [
        r"\begin{table*}[t]", r"    \centering", r"    \scriptsize", r"    \setlength{\tabcolsep}{3.5pt}",
        f"    \\caption{{{caption}}}",
        r"    \label{tab:unified-settings}", r"    \begin{tabular}{@{}", r"        l|ccc|ccc|ccc@{}}", r"        \toprule",
        r"        Method & $r$ & $r_b$ & $r-r_b$ & $\rho$ & $\rho_b$ & $\rho-\rho_b$ & $\tau$ & $\tau_b$ & $\tau-\tau_b$ \\",
        r"        \midrule", r"        \midrule"
    ]
    
    for metric in METRICS:
        if metric not in res_base and metric not in res_plus: continue
            
        row_str = f"        {utils.LATEX_METHOD_MAP.get(metric, metric):<23} & "
        cols = []
        
        for corr_type in ["pearson", "spearman", "kendall"]:
            base_data = res_base.get(metric, {}).get(corr_type, {"r": np.nan, "b": np.nan, "diff": np.nan, "sig": 0})
            plus_data = res_plus.get(metric, {}).get(corr_type, {"r": np.nan, "b": np.nan, "diff": np.nan, "sig": 0})
            
            s_r = f"{utils.fmt(base_data['r'])} / {utils.fmt(plus_data['r'])}" if pd.notna(base_data['r']) or pd.notna(plus_data['r']) else ""
            s_b = f"{utils.fmt(base_data['b'])} / {utils.fmt(plus_data['b'])}" if pd.notna(base_data['b']) or pd.notna(plus_data['b']) else ""
            
            if use_sig:
                str_base = utils.format_sig_str(base_data['diff'], base_data['sig'])
                str_plus = utils.format_sig_str(plus_data['diff'], plus_data['sig'])
            else:
                str_base = utils.format_plain_str(base_data['diff'])
                str_plus = utils.format_plain_str(plus_data['diff'])
                
            s_diff = ""
            if str_base or str_plus:
                s_diff = f"{str_base} / {str_plus}" if str_base and str_plus else f"{str_base} / " if str_base else f" / {str_plus}"
            
            cols.extend([f"{s_r:<15}", f"{s_b:<15}", f"{s_diff:<35}"])
            
        row_str += " & ".join(cols) + r" \\"
        lines.append(row_str)
        
        if metric in ["CBS_F3"]:
            lines.extend([r"        \midrule", r"        \midrule"])
        elif metric in ["ICE-Score w/o r", "CJ-BIN w/o r", "CJ-Score w/o r", "V-BIN w/o r"]:
            lines.append(r"        \midrule")

    lines.extend([
        r"        \bottomrule", r"    \end{tabular}\\", r"    \begin{description}[itemsep=-3pt,topsep=0pt]",
        r"        \item \textbf{CBS}: CodeBERTScore. \hspace{10pt}  \textbf{CJ:} CodeJudge. \hspace{10pt} \textbf{w/o r}: without reference.",
        r"        \item \textbf{Base / Plus format}. $r$: ratio correlation, $r_b$: binary correlation.",
    ])
    
    if use_sig:
        lines.append(r"        \item \textbf{Note}: Bold text indicates significance at $p < 0.05$. Highlighted bold text indicates significance at $p < 0.01$.")
        
    lines.extend([r"    \end{description}", r"\end{table*}"])
    return "\n".join(lines)

OUTPUT_DIR = os.path.join(utils.HERE, "../table_results/table_1")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n_bootstraps", type=int, default=10000)
    parser.add_argument("--mode", type=str, default="default", choices=["default", "avg"], 
                        help="Choose 'default' for original+bootstrap, or 'avg' for per-language average correlation.")
    args = parser.parse_args()
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print("[INFO] Loading and filtering data...")
    df_base = pd.read_csv(utils.X_DATA) if utils.X_DATA.endswith('.csv') else pd.read_parquet(utils.X_DATA)
    df_plus = pd.read_csv(utils.X_PLUS_DATA) if utils.X_PLUS_DATA.endswith('.csv') else pd.read_parquet(utils.X_PLUS_DATA)
    df_plus = df_plus[df_plus["LLM_MODEL"] == "GPT-3.5-Turbo"].copy()
    
    if args.mode == "avg":
        print("\n[PHASE 1 - AVG MODE] Calculating per-language AVERAGE correlations...")
        res_base_avg = utils.get_average_correlation(df_base, "EXECUTION_BASE_BINARY", "EXECUTION_BASE_RATIO", METRICS)
        res_plus_avg = utils.get_average_correlation(df_plus, "EXECUTION_PLUS_BINARY", "EXECUTION_PLUS_RATIO", METRICS)
        
        caption_avg = "The average correlation results calculated independently per language."
        tex_avg = generate_latex(res_base_avg, res_plus_avg, n_bootstraps=0, use_sig=False, custom_caption=caption_avg)
        
        avg_path = os.path.join(OUTPUT_DIR, "correlation_table_avg.tex")
        with open(avg_path, "w", encoding="utf-8") as f:
            f.write(tex_avg)
            
        print(f"Saved Average Correlations to: {avg_path}")
        print("\n[INFO] 'avg' mode complete. Paired bootstraps are skipped.")
    else:
        print("\n[PHASE 1] Calculating ORIGINAL correlations (No Bootstrap)...")
        res_base = utils.get_original_correlations(df_base, "EXECUTION_BASE_BINARY", "EXECUTION_BASE_RATIO", METRICS)
        res_plus = utils.get_original_correlations(df_plus, "EXECUTION_PLUS_BINARY", "EXECUTION_PLUS_RATIO", METRICS)
        
        caption_orig = "The original correlation results without bootstrap."
        tex_original = generate_latex(res_base, res_plus, n_bootstraps=0, use_sig=False, custom_caption=caption_orig)
        original_path = os.path.join(OUTPUT_DIR, "correlation_table_original.tex")
        
        with open(original_path, "w", encoding="utf-8") as f:
            f.write(tex_original)
        print(f"Saved Original Correlations to: {original_path}")

        print(f"\n[PHASE 2] Starting Bootstraps ({args.n_bootstraps} iterations).")
        start_time = time.time()
        
        res_base = utils.apply_bootstraps(df_base, "EXECUTION_BASE_BINARY", "EXECUTION_BASE_RATIO", res_base, METRICS, args.n_bootstraps, pbar_desc="Base Data")
        print("  - Base data bootstraps completed.")
        
        res_plus = utils.apply_bootstraps(df_plus, "EXECUTION_PLUS_BINARY", "EXECUTION_PLUS_RATIO", res_plus, METRICS, args.n_bootstraps, pbar_desc="Plus Data")
        print("  - Plus data bootstraps completed.")
        
        print(f"Bootstrap elapsed time: {time.time() - start_time:.2f} seconds")

        tex_sig = generate_latex(res_base, res_plus, args.n_bootstraps, use_sig=True)
        with open(os.path.join(OUTPUT_DIR, "correlation_table_sig.tex"), "w", encoding="utf-8") as f:
            f.write(tex_sig)
            
        print("\nAll tasks completed successfully! Final tables saved.")