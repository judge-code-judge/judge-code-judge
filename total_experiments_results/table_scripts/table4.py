import pandas as pd
import numpy as np
import argparse
import os
import time
import utils

# 1. 평가 지표 순서 정의 (요청하신 그룹별 순서: V -> ICE -> CJ-BIN -> CJ-Score)
METRICS_ORDER = [
    "V-BIN", "V-BIN w/o r", "V-Score", "V-Score w/o r",
    "ICE-Score", "ICE-Score w/o r", 
    "CJ-BIN", "CJ-BIN w/o r", 
    "CJ-Score", "CJ-Score w/o r"
]

def generate_latex(res_mbpp, res_apps, use_sig=False):
    lines = [
        r"\begin{table*}[!t]",
        r"    \centering",
        r"    \scriptsize",
        r"    \setlength{\tabcolsep}{0.8pt}",
        r"    \caption{The correlation results on MBPP and APPS benchmarks (GPT-5-nano). We present the results in the format of X / Y, where X corresponds to MBPP and Y to APPS.}",
        r"    \label{tab:mbpp-apps}",
        # 기존 table3.py의 컬럼 순서 (Pearson -> Spearman -> Kendall) 유지
        r"    \begin{tabular}{@{}l|ccc|ccc|ccc@{}}",
        r"        \toprule",
        r"        Method & $r$ & $r_b$ & $r-r_b$ & $\rho$ & $\rho_b$ & $\rho-\rho_b$ & $\tau$ & $\tau_b$ & $\tau-\tau_b$ \\"
    ]

    # 여백 조절 (LaTeX 포맷팅용)
    W_METH = 28
    W_VAL  = 16
    W_DIFF = 44

    for i, metric in enumerate(METRICS_ORDER):
        # 그룹이 바뀔 때마다 \midrule 추가 (Vanilla -> ICE -> CJ-BIN -> CJ-Score)
        if i in [0, 4, 6, 8]:
            lines.append(r"        \midrule")

        mbpp_data = res_mbpp.get(metric, {})
        apps_data = res_apps.get(metric, {})
        
        method_name = utils.LATEX_METHOD_MAP.get(metric, metric)
        method_padded = f"{method_name:<{W_METH}}"
        
        cols = []
        for corr_type in ["pearson", "spearman", "kendall"]:
            # 데이터 추출 (없을 경우 NaN 처리)
            m_data = mbpp_data.get(corr_type, {"r": np.nan, "b": np.nan, "diff": np.nan, "sig": 0})
            a_data = apps_data.get(corr_type, {"r": np.nan, "b": np.nan, "diff": np.nan, "sig": 0})
            
            # X / Y 포맷팅 (소수점 3자리)
            s_r = f"{utils.fmt(m_data['r'])} / {utils.fmt(a_data['r'])}"
            s_b = f"{utils.fmt(m_data['b'])} / {utils.fmt(a_data['b'])}"
            
            # Bootstrap 유의성 기호 적용
            if use_sig:
                str_m = utils.format_sig_str(m_data['diff'], m_data['sig'])
                str_a = utils.format_sig_str(a_data['diff'], a_data['sig'])
            else:
                str_m = utils.format_plain_str(m_data['diff'])
                str_a = utils.format_plain_str(a_data['diff'])
                
            s_diff = f"{str_m} / {str_a}"
            
            # 간격 맞추기
            cols.extend([f"{s_r:<{W_VAL}}", f"{s_b:<{W_VAL}}", f"{s_diff:<{W_DIFF}}"])
        
        # 행 문자열 결합
        row_str = f"        {method_padded} & " + " & ".join(cols) + r" \\"
        lines.append(row_str)

    lines.extend([
        r"        \bottomrule",
        r"    \end{tabular}",
        r"\end{table*}"
    ])
    return "\n".join(lines)


OUTPUT_DIR = os.path.join(utils.HERE, "../table_results/table_4")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n_bootstraps", type=int, default=10000)
    args = parser.parse_args()
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print("🚀 [INFO] Loading APPS & MBPP merged data...")
    df = pd.read_csv(utils.APPS_MBPP_DATA) if utils.APPS_MBPP_DATA.endswith('.csv') else pd.read_parquet(utils.APPS_MBPP_DATA)

    df_gpt = df[df["LLM_MODEL"] == "GPT-5-nano"].copy()
    
    # 벤치마크별 분할
    df_mbpp = df_gpt[df_gpt["BENCHMARK"] == "MBPP"].copy()
    df_apps = df_gpt[df_gpt["BENCHMARK"] == "APPS"].copy()
    
    print("\n[PHASE 1] Calculating ORIGINAL correlations (No Bootstrap)...")
    
    # EXECUTION_BINARY, EXECUTION_RATIO 컬럼을 타겟으로 상관계수 추출
    res_mbpp = utils.get_original_correlations(df_mbpp, "EXECUTION_BINARY", "EXECUTION_RATIO", METRICS_ORDER)
    res_apps = utils.get_original_correlations(df_apps, "EXECUTION_BINARY", "EXECUTION_RATIO", METRICS_ORDER)
    
    # 원본 파일 저장 (Bootstrap 미적용)
    tex_original = generate_latex(res_mbpp, res_apps, use_sig=False)
    original_path = os.path.join(OUTPUT_DIR, "table4_mbpp_apps_original.tex")
    with open(original_path, "w", encoding="utf-8") as f:
        f.write(tex_original)
    print(f"Saved Original Table: {original_path}")

    # Bootstrap 시작
    print(f"\n[PHASE 2] Starting Bootstraps ({args.n_bootstraps} iterations). This may take a while...")
    start_time = time.time()
    
    utils.apply_bootstraps(df_mbpp, "EXECUTION_BINARY", "EXECUTION_RATIO", res_mbpp, METRICS_ORDER, args.n_bootstraps, pbar_desc="MBPP")
    utils.apply_bootstraps(df_apps, "EXECUTION_BINARY", "EXECUTION_RATIO", res_apps, METRICS_ORDER, args.n_bootstraps, pbar_desc="APPS")
    
    print(f"Bootstrap elapsed time: {time.time() - start_time:.2f} seconds")

    # Bootstrap 결과(유의성 \hl, \textbf 적용) 저장
    tex_sig = generate_latex(res_mbpp, res_apps, use_sig=True)
    sig_path = os.path.join(OUTPUT_DIR, "table4_mbpp_apps_sig.tex")
    with open(sig_path, "w", encoding="utf-8") as f:
        f.write(tex_sig)
        
    print(f"Saved Significant Table: {sig_path}")
    print("\nAll tasks completed successfully! Table 4 generated.")