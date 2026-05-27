# README

1. Set environment        
        ```jsx
        pip install -r requirements.txt
        ```
        
2. Get pass ratio        
        ```jsx
        python ice_execute_scripts/replace_execution.py
        ```
        
3. LLM-as-a-Judge    
    ```jsx
    cd codejudge_script
    bash humaneval/sample_scripts/table_2/python.sh
    ```
    
4. Generate table
    All results are already in total_experiments_results/table_results/, but if needed, you can obtain new results through the steps below.
    1. merge results
        
        ```jsx
        cd total_experiments_results/X_data/
        python make.py
        ```
        
    2. get pair bootstraps results 
        
        ```jsx
        cd total_experiments_results/table_scripts/
        python table1.py
        ```