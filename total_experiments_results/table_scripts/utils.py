import pandas as pd
import numpy as np
from scipy.stats import pearsonr, kendalltau, rankdata
from tqdm import tqdm
import os

HERE = os.path.dirname(os.path.abspath(__file__))
X_DATA = os.path.join(HERE, "../X_data/HumanEval_X_Total_Results.parquet")
X_PLUS_DATA = os.path.join(HERE, "../X_plus_data/HumanEval_X_Plus_Total_Results.parquet")
APPS_MBPP_DATA = os.path.join(HERE, "../more_benchmark_data/APPS_MBPP_Total_Results.parquet")

LANGUAGES = ["python", "java", "cpp", "js", "go"]

LANG_DISPLAY_MAP = {
    "python": "Python",
    "java": "Java",
    "cpp": "C++",
    "js": "JavaScript",
    "go": "Go"
}

LATEX_METHOD_MAP = {
    "BLEU": "BLEU", "ROUGE-L": "ROUGE-L", "METEOR": "METEOR", "chrF": "chrF",
    "CodeBLEU": "CodeBLEU", "RUBY": "RUBY", 
    "CBS_F1": r"CBS$_{F_1}$", "CBS_F3": r"CBS$_{F_3}$",
    "ICE-Score": "ICE-Score", "ICE-Score w/o r": "ICE-Score w/o r",
    "CJ-BIN": r"\textsc{CJ-Bin}", "CJ-BIN w/o r": r"\textsc{CJ-Bin} w/o r",
    "CJ-Score": r"\textsc{CJ-Score}", "CJ-Score w/o r": r"\textsc{CJ-Score} w/o r",
    "V-BIN": r"\textsc{V-Bin}", "V-BIN w/o r": r"\textsc{V-Bin} w/o r",
    "V-Score": r"\textsc{V-Score}", "V-Score w/o r": r"\textsc{V-Score} w/o r"
}

def compute_metric_corr(metric_name, x, y):
    if len(set(x)) <= 1 or len(set(y)) <= 1: 
        return np.nan
    try:
        if metric_name == "pearson": 
            return pearsonr(x, y)[0]
        elif metric_name == "spearman": 
            return pearsonr(rankdata(x), rankdata(y))[0]
        elif metric_name == "kendall": 
            return kendalltau(x, y)[0]
    except Exception: 
        pass
    return np.nan

def get_filtered_data(df, metric, binary_col, ratio_col):
    valid_df = df[[metric, binary_col, ratio_col]].dropna()
    if valid_df.empty: 
        return None, None, None

    if metric in ["ICE-Score", "ICE-Score w/o r", "V-Score", "V-Score w/o r"]:
        valid_df = valid_df[(valid_df[metric] >= 0) & (valid_df[metric] <= 4)]
    elif metric in ["CJ-BIN", "CJ-BIN w/o r", "V-BIN", "V-BIN w/o r"]:
        valid_df = valid_df[valid_df[metric].isin([0, 1])]
    elif metric in ["CJ-Score", "CJ-Score w/o r"]:
        valid_df = valid_df[(valid_df[metric] >= 0) & (valid_df[metric] <= 1)]

    if valid_df.empty: 
        return None, None, None
        
    return valid_df[metric].values, valid_df[binary_col].values, valid_df[ratio_col].values

def get_original_correlations(df, binary_col, ratio_col, metrics):
    results = {}
    for metric in metrics:
        if metric not in df.columns: continue
            
        score, binary, ratio = get_filtered_data(df, metric, binary_col, ratio_col)
        if score is None: continue
        
        results[metric] = {}
        for corr_name in ["pearson", "spearman", "kendall"]:
            b = compute_metric_corr(corr_name, score, binary)
            r = compute_metric_corr(corr_name, score, ratio)
            diff = r - b if pd.notna(r) and pd.notna(b) else np.nan
            results[metric][corr_name] = {"r": r, "b": b, "diff": diff, "sig": 0}
            
    return results

def get_average_correlation(df, binary_col, ratio_col, metrics):
    all_sample_results = []
    
    for lang in LANGUAGES:
        if "LANG" not in df.columns:
            continue
            
        df_lang = df[df["LANG"] == lang]
        if df_lang.empty:
            continue
        
        # 1. SAMPLE_ID 컬럼이 존재하는 경우 (고유값을 추출하여 각각 계산)
        if "SAMPLE_ID" in df_lang.columns:
            unique_samples = df_lang["SAMPLE_ID"].dropna().unique()
            for sample_id in unique_samples:
                df_subset = df_lang[df_lang["SAMPLE_ID"] == sample_id]
                if not df_subset.empty:
                    # 표본에 대한 상관계수 계산
                    corr_result = get_original_correlations(df_subset, binary_col, ratio_col, metrics)
                    all_sample_results.append(corr_result)
        
        # 2. SAMPLE_ID 컬럼이 아예 없는 경우 (전체를 하나의 샘플로 취급)
        else:
            corr_result = get_original_correlations(df_lang, binary_col, ratio_col, metrics)
            all_sample_results.append(corr_result)
            
    avg_results = {}
    for metric in metrics:
        avg_results[metric] = {}
        for corr_type in ["pearson", "spearman", "kendall"]:
            r_vals, b_vals = [], []
            
            # 3. 누적된 상관계수 결과들(BASE는 15개, PLUS는 5개)을 모음
            for res in all_sample_results:
                if metric in res:
                    corr_data = res[metric].get(corr_type, {})
                    if pd.notna(corr_data.get("r")): r_vals.append(corr_data["r"])
                    if pd.notna(corr_data.get("b")): b_vals.append(corr_data["b"])
            
            # 4. 산술 평균 계산
            avg_r = np.mean(r_vals) if r_vals else np.nan
            avg_b = np.mean(b_vals) if b_vals else np.nan
            avg_diff = avg_r - avg_b if pd.notna(avg_r) and pd.notna(avg_b) else np.nan
            
            avg_results[metric][corr_type] = {
                "r": avg_r, 
                "b": avg_b, 
                "diff": avg_diff, 
                "sig": 0
            }
            
    return avg_results

def apply_bootstraps(df, binary_col, ratio_col, results_dict, metrics, n_bootstraps=10000, seed=42, pbar_desc="Bootstrapping"):
    np.random.seed(seed)
    valid_metrics = [m for m in metrics if m in results_dict]
    
    if not valid_metrics: return results_dict

    with tqdm(total=len(valid_metrics) * 3, desc=pbar_desc, leave=False) as pbar:
        for metric in valid_metrics:
            score, binary, ratio = get_filtered_data(df, metric, binary_col, ratio_col)
            if score is None: 
                pbar.update(3)
                continue
                
            n = len(score)
            
            for corr_name in ["pearson", "spearman", "kendall"]:
                target_data = results_dict[metric][corr_name]
                if pd.isna(target_data["diff"]): 
                    pbar.update(1)
                    continue 
                    
                diff_distribution = []
                for _ in range(n_bootstraps):
                    idx = np.random.choice(n, n, replace=True)
                    s_star, b_star, r_star = score[idx], binary[idx], ratio[idx]
                    
                    if len(set(s_star)) <= 1 or len(set(b_star)) <= 1 or len(set(r_star)) <= 1: 
                        continue
                        
                    try:
                        corr_b_star = compute_metric_corr(corr_name, s_star, b_star)
                        corr_r_star = compute_metric_corr(corr_name, s_star, r_star)
                        diff_distribution.append(corr_r_star - corr_b_star)
                    except Exception: 
                        continue
                        
                if len(diff_distribution) >= (n_bootstraps * 0.5): 
                    diff_distribution = np.array(diff_distribution)
                    a95, b95 = np.percentile(diff_distribution, [2.5, 97.5])
                    a99, b99 = np.percentile(diff_distribution, [0.5, 99.5])
                    
                    if (a99 > 0) or (b99 < 0):
                        target_data["sig"] = 2  # 99% CI 유의
                    elif (a95 > 0) or (b95 < 0):
                        target_data["sig"] = 1 # 95% CI 유의

                pbar.update(1)
                
    return results_dict

def fmt(x): 
    return f"{x:.3f}" if not pd.isna(x) else "N/A"

def format_plain_str(val):
    if pd.isna(val): return "N/A"
    return f"{val:+.3f}" if val != 0 else "0.000"

def format_sig_str(val, sig_level):
    if pd.isna(val): return "N/A"
    s = f"{val:+.3f}" if val != 0 else "0.000"
    
    if sig_level == 2:   return rf"\hl{{\textbf{{{s}}}}}"
    elif sig_level == 1: return rf"\textbf{{{s}}}"
    return s