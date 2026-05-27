import os
import re

def split_latex_table(input_file="correlation_table_sig.tex"):
    if not os.path.exists(input_file):
        print(f"[오류] {input_file} 파일을 찾을 수 없습니다.")
        return

    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 출력할 3가지 지표 설정 (기호, & 스플릿 후 시작 인덱스, 끝 인덱스)
    metrics = {
        'r': {'symbol': 'r', 'col_start': 2, 'col_end': 5},
        'rho': {'symbol': '\\rho', 'col_start': 5, 'col_end': 8},
        'tau': {'symbol': '\\tau', 'col_start': 8, 'col_end': 11}
    }

    # 정규식으로 \begin{tabular} ~ \end{tabular} 안의 데이터만 추출
    tabular_match = re.search(r'\\begin\{tabular\}\{.*?\}(.*?)\\end\{tabular\}', content, re.DOTALL)
    if not tabular_match:
        print("[오류] tabular 환경을 찾을 수 없습니다.")
        return

    tabular_content = tabular_match.group(1)
    lines = tabular_content.split('\n')
    
    # 3개 파일에 들어갈 각각의 라인 리스트
    tabular_lines = { 'r': [], 'rho': [], 'tau': [] }

    row_buffer = ""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
            
        # 단일 라인 사선 및 구분선 처리 (\toprule, \midrule, \bottomrule)
        if stripped.startswith('\\midrule') or stripped.startswith('\\toprule') or stripped.startswith('\\bottomrule'):
            for m in metrics: 
                tabular_lines[m].append("        " + stripped)
            continue
            
        # \cmidrule은 컬럼 수가 11개에서 5개로 줄어들었으므로 2-5로 고정
        if stripped.startswith('\\cmidrule'):
            for m in metrics: 
                tabular_lines[m].append("        \\cmidrule{2-5}")
            continue

        # 버퍼에 누적
        row_buffer += " " + stripped
        
        # 라인의 끝이 '\\' 이면 하나의 행(row)이 완성된 것으로 판단
        if row_buffer.endswith('\\\\'):
            row_content = row_buffer[:-2]
            cols = [c.strip() for c in row_content.split('&')]
            
            if len(cols) >= 11:
                col0 = cols[0]
                col1 = cols[1]
                is_header = "Method" in col1
                
                for m, info in metrics.items():
                    start = info['col_start']
                    end = info['col_end']
                    
                    if is_header:
                        sym = info['symbol']
                        sym_b = f"{sym}_b"
                        row_str = f"         & {col1:<23} & ${sym}${'':<13} & ${sym_b}${'':<12} & ${sym}-{sym_b}${'':<30} \\\\"
                        tabular_lines[m].append(row_str)
                    else:
                        selected_cols = cols[start:end]
                        c0 = col0.strip()
                        
                        # 핵심 수정 포인트: \multirow가 있으면 자신의 줄에 단독으로 출력
                        if c0:
                            tabular_lines[m].append(f"        {c0}")
                        
                        # 데이터는 무조건 들여쓰기 후 '&' 로 시작
                        rest = "  & ".join([f"{c:<14}" for c in selected_cols])
                        row_str = f"         & {col1:<23} & {rest} \\\\"
                        tabular_lines[m].append(row_str)
            else:
                for m in metrics: 
                    tabular_lines[m].append("        " + row_buffer.strip())
                
            row_buffer = ""

    # 추출된 라인들을 요구하신 싱글 컬럼 테이블 껍데기로 감싸서 파일로 저장
    for m, info in metrics.items():
        sym = info['symbol'].replace('\\', '')
        
        table_tex = [
            r"\begin{table}[t]",
            r"    \setlength{\aboverulesep}{0.12ex}",
            r"    \setlength{\belowrulesep}{0.12ex}",
            r"    \setlength{\cmidrulesep}{0ex}",
            r"    \centering",
            r"    \scriptsize",
            r"    \setlength{\tabcolsep}{0.8pt}",
            r"    \caption{The breakdown of the results shown in Table~\ref{tab:recent-llms} by programming language.}",
            f"    \\label{{tab:per-language-{sym}}}",
            r"    \begin{tabular}{@{}c|c|ccc@{}}"
        ]
        
        table_tex.extend(tabular_lines[m])
        
        table_tex.extend([
            r"    \end{tabular}",
            r"    \vspace{-10pt}",
            r"\end{table}"
        ])
        
        out_filename = f"per-language-{sym}.tex"
        with open(out_filename, 'w', encoding='utf-8') as out_f:
            out_f.write("\n".join(table_tex))
        print(f"[완료] {out_filename} 파일이 정상적으로 생성되었습니다.")

if __name__ == "__main__":
    split_latex_table()