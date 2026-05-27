import re

def transform_latex_table_aligned(input_tex):
    lang_blocks = {}
    current_lang = None
    in_tabular = False
    header_passed = False

    # 1. 원본 LaTeX 텍스트 파싱
    for line in input_tex.strip().split('\n'):
        line = line.strip()
        if not line:
            continue

        lang_match = re.search(r'\{\\bf\s+([^}]+)\}', line)
        if lang_match:
            current_lang = lang_match.group(1)
            lang_blocks[current_lang] = []
            continue

        if current_lang:
            if r"\begin{tabular}" in line:
                in_tabular = True
                header_passed = False
                continue

            if in_tabular:
                if r"\bottomrule" in line:
                    in_tabular = False
                    current_lang = None
                    continue

                if not header_passed:
                    if r"\midrule" in line:
                        header_passed = True
                    continue

                lang_blocks[current_lang].append(line)

    # 2. 정렬을 위한 각 열(Column)의 최대 길이 계산
    # 총 11개 열: [언어(Multirow), Method, r, r_b, r-r_b, rho, rho_b, rho-rho_b, tau, tau_b, tau-tau_b]
    col_widths = [0] * 11
    headers = ["", "Method", "$r$", "$r_b$", "$r-r_b$", "$\\rho$", "$\\rho_b$", "$\\rho-\\rho_b$", "$\\tau$", "$\\tau_b$", "$\\tau-\\tau_b$"]
    
    for i, h in enumerate(headers):
        col_widths[i] = max(col_widths[i], len(h))

    parsed_blocks = {}
    for lang, lines in lang_blocks.items():
        parsed_blocks[lang] = []
        first_row = True
        
        # midrule을 제외한 실제 데이터 행 개수 파악
        data_row_count = len([l for l in lines if l != r"\midrule"])
        
        for line in lines:
            if line == r"\midrule":
                parsed_blocks[lang].append({'type': 'midrule'})
                continue
            
            # 후행 \\ 및 공백 제거 후 분리
            row_content = line.replace(r'\\', '').strip()
            cols = [c.strip() for c in row_content.split('&')]
            
            # 첫 번째 열(언어) 생성
            multirow_str = f"\\multirow{{{data_row_count}}}{{*}}{{\\rotatebox{{90}}{{{lang}}}}}" if first_row else ""
            row_cols = [multirow_str] + cols
            
            parsed_blocks[lang].append({'type': 'data', 'cols': row_cols})
            
            # 최대 길이 업데이트 (hl, textbf 등의 태그 길이도 포함하여 계산)
            for i, c in enumerate(row_cols):
                if i < 11:
                    col_widths[i] = max(col_widths[i], len(c))
                    
            first_row = False

    # 3. 완벽하게 정렬된 출력물 생성
    output = []
    output.append(r"    \begin{tabular}{@{}c|c|ccc|ccc|ccc@{}}")
    output.append(r"        \toprule")
    
    # 헤더 행 정렬
    head_cols = [h.ljust(col_widths[i]) for i, h in enumerate(headers)]
    output.append("        " + " & ".join(head_cols) + r" \\")
    
    for lang, items in parsed_blocks.items():
        output.append(r"        \midrule")
        output.append(r"        \midrule")
        
        for item in items:
            if item['type'] == 'midrule':
                output.append(r"        \cmidrule{2-11}")
            else:
                cols = item['cols']
                # 모든 텍스트를 자신이 속한 열의 최대 길이에 맞춰 공백(ljust)으로 채움
                formatted_cols = [c.ljust(col_widths[i]) for i, c in enumerate(cols)]
                
                # "&"를 기준으로 합치고 마지막에 "\\" 추가 (모두 완벽하게 일직선 정렬됨)
                output.append("        " + " & ".join(formatted_cols) + r" \\")
                
    output.append(r"        \bottomrule")
    output.append(r"    \end{tabular}")

    return "\n".join(output)

# --- 사용 예시 ---
if __name__ == "__main__":
    # 파일을 읽어오거나 문자열을 직접 넣습니다.
    # with open('input_table.tex', 'r', encoding='utf-8') as f:
    #     raw_latex = f.read()

    raw_latex = r"""
    {\bf Python}\\
    \begin{tabular}{@{}l|ccc|ccc|ccc@{}}
        \toprule
        Method & $r$ & $r_b$ & $r-r_b$ & $\rho$ & $\rho_b$ & $\rho-\rho_b$ & $\tau$ & $\tau_b$ & $\tau-\tau_b$ \\
        \midrule
        ICE-Score               & 0.693 / 0.645   & 0.761 / 0.615   & \hl{\textbf{-0.068}} / \hl{\textbf{+0.030}} & 0.746 / 0.681   & 0.736 / 0.640   & +0.010 / \hl{\textbf{+0.041}}       & 0.639 / 0.579   & 0.678 / 0.593   & \hl{\textbf{-0.039}} / -0.014       \\
        ICE-Score w/o r         & 0.639 / 0.610   & 0.674 / 0.530   & \hl{\textbf{-0.035}} / \hl{\textbf{+0.080}} & 0.689 / 0.628   & 0.657 / 0.557   & \hl{\textbf{+0.032}} / \hl{\textbf{+0.071}} & 0.584 / 0.535   & 0.602 / 0.518   & \hl{\textbf{-0.018}} / \textbf{+0.017} \\
        \midrule
        \textsc{CJ-Bin}         & 0.637 / 0.637   & 0.809 / 0.764   & \hl{\textbf{-0.172}} / \hl{\textbf{-0.127}} & 0.722 / 0.703   & 0.809 / 0.764   & \hl{\textbf{-0.087}} / \hl{\textbf{-0.061}} & 0.631 / 0.615   & 0.809 / 0.764   & \hl{\textbf{-0.178}} / \hl{\textbf{-0.149}} \\
        \textsc{CJ-Bin} w/o r   & 0.603 / 0.639   & 0.737 / 0.755   & \hl{\textbf{-0.134}} / \hl{\textbf{-0.116}} & 0.678 / 0.704   & 0.737 / 0.755   & \hl{\textbf{-0.059}} / \hl{\textbf{-0.051}} & 0.593 / 0.615   & 0.737 / 0.755   & \hl{\textbf{-0.144}} / \hl{\textbf{-0.139}} \\
        \textsc{CJ-Score}       & 0.754 / 0.449   & 0.732 / 0.372   & \textbf{+0.021} / \hl{\textbf{+0.077}} & 0.810 / 0.474   & 0.770 / 0.386   & \hl{\textbf{+0.041}} / \hl{\textbf{+0.089}} & 0.705 / 0.413   & 0.708 / 0.365   & -0.004 / \hl{\textbf{+0.047}}       \\
        \textsc{CJ-Score} w/o r & 0.742 / 0.411   & 0.691 / 0.328   & \hl{\textbf{+0.052}} / \hl{\textbf{+0.083}} & 0.773 / 0.424   & 0.705 / 0.333   & \hl{\textbf{+0.068}} / \hl{\textbf{+0.091}} & 0.659 / 0.366   & 0.639 / 0.315   & \hl{\textbf{+0.020}} / \hl{\textbf{+0.052}} \\
        \midrule
        \textsc{V-Bin}          & 0.607 / 0.458   & 0.787 / 0.569   & \hl{\textbf{-0.180}} / \hl{\textbf{-0.111}} & 0.690 / 0.512   & 0.787 / 0.569   & \hl{\textbf{-0.097}} / \hl{\textbf{-0.057}} & 0.603 / 0.448   & 0.787 / 0.569   & \hl{\textbf{-0.184}} / \hl{\textbf{-0.121}} \\
        \textsc{V-Bin} w/o r    & 0.549 / 0.523   & 0.674 / 0.582   & \hl{\textbf{-0.126}} / \hl{\textbf{-0.059}} & 0.613 / 0.564   & 0.674 / 0.582   & \hl{\textbf{-0.061}} / \textbf{-0.019} & 0.536 / 0.493   & 0.674 / 0.582   & \hl{\textbf{-0.138}} / \hl{\textbf{-0.089}} \\
        \textsc{V-Score}        & 0.611 / -0.002  & 0.692 / 0.027   & \hl{\textbf{-0.081}} / \textbf{-0.029} & 0.660 / 0.010   & 0.667 / 0.027   & -0.007 / -0.017                     & 0.550 / 0.008   & 0.602 / 0.025   & \hl{\textbf{-0.052}} / \textbf{-0.017} \\
        \textsc{V-Score} w/o r  & 0.570 / 0.016   & 0.617 / 0.013   & \hl{\textbf{-0.048}} / +0.003       & 0.609 / 0.009   & 0.599 / 0.002   & +0.010 / +0.008                     & 0.502 / 0.008   & 0.537 / 0.002   & \hl{\textbf{-0.035}} / +0.006       \\
        \bottomrule
    \end{tabular}
    \\[7pt]
    {\bf Java}\\
    \begin{tabular}{@{}l|ccc|ccc|ccc@{}}
        \toprule
        Method & $r$ & $r_b$ & $r-r_b$ & $\rho$ & $\rho_b$ & $\rho-\rho_b$ & $\tau$ & $\tau_b$ & $\tau-\tau_b$ \\
        \midrule
        ICE-Score               & 0.717 / 0.687   & 0.739 / 0.600   & \textbf{-0.022} / \hl{\textbf{+0.087}} & 0.726 / 0.708   & 0.670 / 0.632   & \hl{\textbf{+0.056}} / \hl{\textbf{+0.076}} & 0.620 / 0.603   & 0.616 / 0.592   & +0.004 / +0.012                     \\
        ICE-Score w/o r         & 0.622 / 0.665   & 0.608 / 0.555   & +0.015 / \hl{\textbf{+0.111}}       & 0.628 / 0.695   & 0.556 / 0.587   & \hl{\textbf{+0.072}} / \hl{\textbf{+0.108}} & 0.529 / 0.592   & 0.510 / 0.548   & \textbf{+0.020} / \hl{\textbf{+0.044}} \\
        \midrule
        \textsc{CJ-Bin}         & 0.219 / 0.500   & 0.221 / 0.612   & -0.003 / \hl{\textbf{-0.112}}       & 0.221 / 0.532   & 0.221 / 0.612   & -0.000 / \hl{\textbf{-0.080}}       & 0.193 / 0.464   & 0.221 / 0.612   & \textbf{-0.029} / \hl{\textbf{-0.148}} \\
        \textsc{CJ-Bin} w/o r   & 0.264 / 0.468   & 0.325 / 0.577   & \hl{\textbf{-0.061}} / \hl{\textbf{-0.109}} & 0.282 / 0.500   & 0.325 / 0.577   & \hl{\textbf{-0.043}} / \hl{\textbf{-0.077}} & 0.246 / 0.436   & 0.325 / 0.577   & \hl{\textbf{-0.079}} / \hl{\textbf{-0.141}} \\
        \textsc{CJ-Score}       & 0.756 / 0.417   & 0.649 / 0.194   & \hl{\textbf{+0.107}} / \hl{\textbf{+0.224}} & 0.800 / 0.437   & 0.657 / 0.194   & \hl{\textbf{+0.143}} / \hl{\textbf{+0.243}} & 0.702 / 0.375   & 0.605 / 0.179   & \hl{\textbf{+0.097}} / \hl{\textbf{+0.196}} \\
        \textsc{CJ-Score} w/o r & 0.709 / 0.152   & 0.616 / -0.036  & \hl{\textbf{+0.094}} / \hl{\textbf{+0.188}} & 0.744 / 0.165   & 0.606 / -0.053  & \hl{\textbf{+0.138}} / \hl{\textbf{+0.218}} & 0.635 / 0.144   & 0.552 / -0.051  & \hl{\textbf{+0.083}} / \hl{\textbf{+0.195}} \\
        \midrule
        \textsc{V-Bin}          & 0.607 / 0.586   & 0.744 / 0.646   & \hl{\textbf{-0.137}} / \hl{\textbf{-0.060}} & 0.643 / 0.606   & 0.744 / 0.646   & \hl{\textbf{-0.101}} / \hl{\textbf{-0.040}} & 0.561 / 0.528   & 0.744 / 0.646   & \hl{\textbf{-0.183}} / \hl{\textbf{-0.118}} \\
        \textsc{V-Bin} w/o r    & 0.504 / 0.551   & 0.577 / 0.587   & \hl{\textbf{-0.073}} / \hl{\textbf{-0.036}} & 0.531 / 0.567   & 0.577 / 0.587   & \hl{\textbf{-0.046}} / -0.020       & 0.463 / 0.495   & 0.577 / 0.587   & \hl{\textbf{-0.114}} / \hl{\textbf{-0.093}} \\
        \textsc{V-Score}        & 0.600 / 0.629   & 0.633 / 0.584   & \hl{\textbf{-0.032}} / \hl{\textbf{+0.045}} & 0.585 / 0.643   & 0.532 / 0.576   & \hl{\textbf{+0.053}} / \hl{\textbf{+0.067}} & 0.492 / 0.541   & 0.478 / 0.537   & +0.015 / +0.004                     \\
        \textsc{V-Score} w/o r  & 0.477 / -0.033  & 0.480 / -0.025  & -0.003 / -0.008                     & 0.447 / -0.030  & 0.398 / -0.024  & \hl{\textbf{+0.049}} / -0.006       & 0.368 / -0.026  & 0.356 / -0.023  & +0.012 / -0.002                     \\
        \bottomrule
    \end{tabular}
    \\[7pt]
    {\bf C++}\\
    \begin{tabular}{@{}l|ccc|ccc|ccc@{}}
        \toprule
        Method & $r$ & $r_b$ & $r-r_b$ & $\rho$ & $\rho_b$ & $\rho-\rho_b$ & $\tau$ & $\tau_b$ & $\tau-\tau_b$ \\
        \midrule
        ICE-Score               & 0.665 / 0.562   & 0.698 / 0.533   & \hl{\textbf{-0.033}} / \textbf{+0.029} & 0.674 / 0.569   & 0.615 / 0.565   & \hl{\textbf{+0.059}} / +0.004       & 0.581 / 0.484   & 0.564 / 0.523   & +0.017 / \hl{\textbf{-0.039}}       \\
        ICE-Score w/o r         & 0.648 / 0.625   & 0.651 / 0.550   & -0.003 / \hl{\textbf{+0.075}}       & 0.668 / 0.644   & 0.585 / 0.574   & \hl{\textbf{+0.083}} / \hl{\textbf{+0.070}} & 0.569 / 0.552   & 0.534 / 0.532   & \hl{\textbf{+0.035}} / \textbf{+0.020} \\
        \midrule
        \textsc{CJ-Bin}         & 0.227 / 0.409   & 0.286 / 0.531   & \hl{\textbf{-0.059}} / \hl{\textbf{-0.122}} & 0.241 / 0.421   & 0.286 / 0.531   & \hl{\textbf{-0.045}} / \hl{\textbf{-0.109}} & 0.214 / 0.374   & 0.286 / 0.531   & \hl{\textbf{-0.072}} / \hl{\textbf{-0.157}} \\
        \textsc{CJ-Bin} w/o r   & 0.189 / 0.348   & 0.213 / 0.439   & -0.024 / \hl{\textbf{-0.090}}       & 0.200 / 0.363   & 0.213 / 0.439   & -0.013 / \hl{\textbf{-0.076}}       & 0.177 / 0.321   & 0.213 / 0.439   & \hl{\textbf{-0.036}} / \hl{\textbf{-0.117}} \\
        \textsc{CJ-Score}       & 0.667 / 0.413   & 0.614 / 0.303   & \hl{\textbf{+0.053}} / \hl{\textbf{+0.110}} & 0.708 / 0.423   & 0.584 / 0.323   & \hl{\textbf{+0.123}} / \hl{\textbf{+0.100}} & 0.623 / 0.362   & 0.541 / 0.295   & \hl{\textbf{+0.082}} / \hl{\textbf{+0.066}} \\
        \textsc{CJ-Score} w/o r & 0.595 / 0.290   & 0.538 / 0.134   & \hl{\textbf{+0.057}} / \hl{\textbf{+0.156}} & 0.618 / 0.292   & 0.489 / 0.096   & \hl{\textbf{+0.129}} / \hl{\textbf{+0.196}} & 0.537 / 0.254   & 0.454 / 0.090   & \hl{\textbf{+0.083}} / \hl{\textbf{+0.163}} \\
        \midrule
        \textsc{V-Bin}          & 0.564 / 0.495   & 0.700 / 0.583   & \hl{\textbf{-0.136}} / \hl{\textbf{-0.088}} & 0.594 / 0.510   & 0.700 / 0.583   & \hl{\textbf{-0.106}} / \hl{\textbf{-0.073}} & 0.527 / 0.452   & 0.700 / 0.583   & \hl{\textbf{-0.174}} / \hl{\textbf{-0.131}} \\
        \textsc{V-Bin} w/o r    & 0.452 / 0.425   & 0.571 / 0.514   & \hl{\textbf{-0.120}} / \hl{\textbf{-0.088}} & 0.492 / 0.443   & 0.571 / 0.514   & \hl{\textbf{-0.080}} / \hl{\textbf{-0.071}} & 0.436 / 0.393   & 0.571 / 0.514   & \hl{\textbf{-0.135}} / \hl{\textbf{-0.121}} \\
        \textsc{V-Score}        & 0.530 / 0.561   & 0.547 / 0.553   & -0.017 / +0.009                     & 0.487 / 0.555   & 0.435 / 0.542   & \hl{\textbf{+0.052}} / +0.012       & 0.411 / 0.470   & 0.391 / 0.504   & +0.020 / \hl{\textbf{-0.033}}       \\
        \textsc{V-Score} w/o r  & 0.395 / -0.024  & 0.378 / 0.013   & +0.017 / \hl{\textbf{-0.037}}       & 0.359 / -0.003  & 0.302 / 0.016   & \hl{\textbf{+0.057}} / -0.019       & 0.300 / -0.003  & 0.274 / 0.015   & \textbf{+0.026} / -0.018            \\
        \bottomrule
    \end{tabular}
    \\[7pt]
    {\bf JavaScript}\\
    \begin{tabular}{@{}l|ccc|ccc|ccc@{}}
        \toprule
        Method & $r$ & $r_b$ & $r-r_b$ & $\rho$ & $\rho_b$ & $\rho-\rho_b$ & $\tau$ & $\tau_b$ & $\tau-\tau_b$ \\
        \midrule
        ICE-Score               & 0.545 / 0.503   & 0.639 / 0.465   & \hl{\textbf{-0.094}} / \hl{\textbf{+0.039}} & 0.593 / 0.547   & 0.611 / 0.502   & -0.018 / \hl{\textbf{+0.044}}       & 0.489 / 0.444   & 0.556 / 0.461   & \hl{\textbf{-0.066}} / -0.017       \\
        ICE-Score w/o r         & 0.512 / 0.414   & 0.580 / 0.419   & \hl{\textbf{-0.068}} / -0.005       & 0.559 / 0.452   & 0.566 / 0.428   & -0.007 / \textbf{+0.024}            & 0.459 / 0.371   & 0.515 / 0.402   & \hl{\textbf{-0.056}} / \hl{\textbf{-0.031}} \\
        \midrule
        \textsc{CJ-Bin}         & 0.530 / 0.429   & 0.703 / 0.573   & \hl{\textbf{-0.172}} / \hl{\textbf{-0.144}} & 0.600 / 0.492   & 0.703 / 0.573   & \hl{\textbf{-0.103}} / \hl{\textbf{-0.081}} & 0.513 / 0.421   & 0.703 / 0.573   & \hl{\textbf{-0.190}} / \hl{\textbf{-0.152}} \\
        \textsc{CJ-Bin} w/o r   & 0.467 / 0.448   & 0.614 / 0.565   & \hl{\textbf{-0.147}} / \hl{\textbf{-0.117}} & 0.526 / 0.501   & 0.614 / 0.565   & \hl{\textbf{-0.088}} / \hl{\textbf{-0.063}} & 0.450 / 0.428   & 0.614 / 0.565   & \hl{\textbf{-0.164}} / \hl{\textbf{-0.136}} \\
        \textsc{CJ-Score}       & 0.623 / 0.456   & 0.569 / 0.336   & \hl{\textbf{+0.054}} / \hl{\textbf{+0.120}} & 0.681 / 0.491   & 0.625 / 0.341   & \hl{\textbf{+0.056}} / \hl{\textbf{+0.150}} & 0.574 / 0.417   & 0.570 / 0.327   & +0.004 / \hl{\textbf{+0.091}}       \\
        \textsc{CJ-Score} w/o r & 0.563 / 0.354   & 0.514 / 0.166   & \hl{\textbf{+0.049}} / \hl{\textbf{+0.188}} & 0.594 / 0.382   & 0.534 / 0.170   & \hl{\textbf{+0.060}} / \hl{\textbf{+0.212}} & 0.486 / 0.324   & 0.480 / 0.166   & +0.006 / \hl{\textbf{+0.158}}       \\
        \midrule
        \textsc{V-Bin}          & 0.493 / 0.389   & 0.692 / 0.525   & \hl{\textbf{-0.199}} / \hl{\textbf{-0.136}} & 0.559 / 0.446   & 0.692 / 0.525   & \hl{\textbf{-0.133}} / \hl{\textbf{-0.079}} & 0.478 / 0.381   & 0.692 / 0.525   & \hl{\textbf{-0.214}} / \hl{\textbf{-0.144}} \\
        \textsc{V-Bin} w/o r    & 0.420 / 0.061   & 0.571 / 0.059   & \hl{\textbf{-0.150}} / +0.002       & 0.473 / 0.061   & 0.571 / 0.059   & \hl{\textbf{-0.098}} / +0.002       & 0.404 / 0.052   & 0.571 / 0.059   & \hl{\textbf{-0.167}} / -0.007       \\
        \textsc{V-Score}        & 0.454 / 0.539   & 0.534 / 0.516   & \hl{\textbf{-0.080}} / +0.023       & 0.486 / 0.577   & 0.499 / 0.526   & -0.013 / \hl{\textbf{+0.051}}       & 0.395 / 0.466   & 0.449 / 0.477   & \hl{\textbf{-0.054}} / -0.011       \\
        \textsc{V-Score} w/o r  & 0.355 / -0.012  & 0.413 / -0.045  & \hl{\textbf{-0.058}} / \hl{\textbf{+0.034}} & 0.372 / -0.017  & 0.382 / -0.045  & -0.009 / \hl{\textbf{+0.029}}       & 0.301 / -0.014  & 0.343 / -0.045  & \hl{\textbf{-0.042}} / \hl{\textbf{+0.031}} \\
        \bottomrule
    \end{tabular}
    \\[7pt]
    {\bf Go}\\
    \begin{tabular}{@{}l|ccc|ccc|ccc@{}}
        \toprule
        Method & $r$ & $r_b$ & $r-r_b$ & $\rho$ & $\rho_b$ & $\rho-\rho_b$ & $\tau$ & $\tau_b$ & $\tau-\tau_b$ \\
        \midrule
        ICE-Score               & 0.036 / 0.162   & 0.047 / 0.145   & -0.011 / +0.017                     & 0.051 / 0.167   & 0.051 / 0.142   & +0.000 / \hl{\textbf{+0.025}}       & 0.044 / 0.145   & 0.051 / 0.141   & -0.007 / +0.004                     \\
        ICE-Score w/o r         & 0.079 / 0.056   & 0.073 / 0.038   & +0.006 / +0.019                     & 0.081 / 0.053   & 0.072 / 0.039   & +0.008 / \textbf{+0.014}            & 0.069 / 0.046   & 0.072 / 0.039   & -0.002 / +0.007                     \\
        \midrule
        \textsc{CJ-Bin}         & 0.017 / 0.020   & -0.000 / 0.029  & +0.018 / \hl{\textbf{-0.010}}       & 0.010 / 0.026   & -0.000 / 0.029  & +0.011 / \hl{\textbf{-0.003}}       & 0.009 / 0.023   & -0.000 / 0.029  & +0.010 / \hl{\textbf{-0.006}}       \\
        \textsc{CJ-Bin} w/o r   & 0.029 / N/A     & 0.014 / N/A     & +0.015 / N/A                        & 0.023 / N/A     & 0.014 / N/A     & +0.010 / N/A                        & 0.020 / N/A     & 0.014 / N/A     & +0.007 / N/A                        \\
        \textsc{CJ-Score}       & -0.004 / -0.107 & -0.003 / -0.141 & -0.002 / \textbf{+0.034}            & -0.016 / -0.139 & -0.009 / -0.148 & -0.007 / +0.009                     & -0.014 / -0.121 & -0.009 / -0.148 & -0.005 / \hl{\textbf{+0.027}}       \\
        \textsc{CJ-Score} w/o r & 0.007 / -0.039  & -0.022 / -0.041 & \hl{\textbf{+0.030}} / +0.001       & -0.006 / -0.042 & -0.022 / -0.039 & \hl{\textbf{+0.016}} / -0.003       & -0.005 / -0.036 & -0.022 / -0.039 & \hl{\textbf{+0.017}} / +0.003       \\
        \midrule
        \textsc{V-Bin}          & 0.014 / N/A     & 0.003 / N/A     & +0.010 / N/A                        & 0.008 / N/A     & 0.003 / N/A     & +0.004 / N/A                        & 0.007 / N/A     & 0.003 / N/A     & +0.003 / N/A                        \\
        \textsc{V-Bin} w/o r    & N/A / N/A       & N/A / N/A       & N/A / N/A                           & N/A / N/A       & N/A / N/A       & N/A / N/A                           & N/A / N/A       & N/A / N/A       & N/A / N/A                           \\
        \textsc{V-Score}        & 0.127 / 0.017   & 0.140 / 0.029   & -0.013 / -0.012                     & 0.145 / 0.031   & 0.145 / 0.027   & -0.000 / +0.004                     & 0.120 / 0.025   & 0.139 / 0.024   & \textbf{-0.018} / +0.000            \\
        \textsc{V-Score} w/o r  & 0.019 / 0.004   & 0.037 / 0.001   & -0.018 / +0.003                     & 0.129 / 0.000   & 0.128 / 0.001   & +0.001 / -0.001                     & 0.107 / 0.000   & 0.121 / 0.001   & -0.014 / -0.000                     \\
        \bottomrule
    \end{tabular}
    """

    unified_latex = transform_latex_table_aligned(raw_latex)
    print(unified_latex)
    
    # 결과를 새 파일로 저장
    # with open('output_unified_table.tex', 'w', encoding='utf-8') as f:
    #     f.write(unified_latex)