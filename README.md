# README

1. Set environment
    ```
    pip install -r requirements.txt
    ```
        
2. Get pass ratio
    ```
    python ice_execute_scripts/replace_execution.py
    ```
        
3. LLM-as-a-Judge
    ```
    cd codejudge_script
    bash humaneval/sample_scripts/table_2/python.sh
    ```
    
4. Generate table
    1. All results are already in total_experiments_results/table_results/, but if needed, you can obtain new results through the steps below.
    2. merge results
        ```
        cd total_experiments_results/X_data/
        python make.py
        ```
        
    3. get pair bootstraps results 
        ```
        cd total_experiments_results/table_scripts/
        python table1.py
        ```