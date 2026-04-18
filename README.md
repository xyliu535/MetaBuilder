# MetaBuilder
## A.使用方法
- 進入 leaderboard 官網，下滑進入“Analyze Results in Detail”，挑選想要研究的模型(目前只支持了Verified)，點擊 load data 並點擊 (logs) 進入其 github 項目頁面，找到 “metadata.yaml” ，裏面的 assets 下會給出這個模型產生的 logs 的下載鏈接，下載至（項目根目錄\Model_data\）即可(https://www.swebench.com/)
- 安裝依賴：
    ```sh 
    python -m venv .venv
    .venv\Scripts\activate  //linux : source .venv/bin/activate
    pip install -r requirements.txt 
    ```
- 運行腳本： 
    ```sh
    python ./scripts/patch_collector.py
    ```
    ```sh
    python ./scripts/meta_builder.py
    ```
- 運行完畢後會在項目根目錄下生成 “cases/” 和 “repos/” 兩個文件夾，分別保存 “所有的cases的patch信息和元數據等” 和 “cases用到的項目”

- 人工審計案例從 "cases/" 中選取即可。

- 如果需要根據 ratios 來篩選 case id ，可以打開 suspicious_collector.py 修改一下需要的指標和閾值，然後執行腳本：
    ```sh
    python ./scripts/suspicious_collector.py
    ```
## B.腳本結構
- 項目結構：
    ```sh
    project/
    ├── scripts/
    │ ├── meta_builder.py
    │ ├── ratios_computer.py
    │ ├── suspicious_collector.py
    │ ├── patch_collector.py
    │ ├── repo_maintainer.py
    │ ├── symbol_locator.py
    │ ├── file_filter.py
    │ ├── comparison_analyzer.py
    │
    ├── Model_data/
    ├── repos/
    ├── cases/
    │
    ├── README.md
    ├── requirements.txt
    └── .gitignore
    ```
## C.腳本功能介紹：
- patch_collector.py：把 "Model_data/<model_name>/logs/" 裏面的所有 cases 複製到 "根目錄/cases/" 裏面，並且聯網從 SWE-Bench Verified 的數據集中把每個 case 對應的人工 patch 下載到 "根目錄/cases/" 對應的文件夾裏。

- file_filter.py：對模型 patch 以及人工 patch 改動涉及的文件進行分類，分爲：代碼文件（CODE），測試文件 （TEST），文檔文件（DOC）和垃圾文件（TRASH）。我們主要對“代碼文件”進行研究，劃分依據主要是文件名稱以及路徑名稱，後續也可以對劃分依據進行一定修改完善。

- repo_maintainer.py：聯網把所有 cases 涉及的 Python 庫下載到 “根目錄/repos/” 中，並且後續可以針對每個 case 當時的 base_commit 來進行 git checkout 到當時的分支。

- comparison_analyzer.py：對每個 case 分析模型 patch 與人工 patch 的差別數據並且記錄。 

- symbol_locator.py：爲每個 case 的模型 patch 和人工 patch 定位 “改動所位於的類/方法”，並且記錄。

- ratios_computer.py：利用現有信息進行一些 ratios 的計算，並且記錄。後續要對指標計算進行修改就可以直接修改此腳本。 

- meta_builder.py：結合上述腳本，爲每個 case 計算、記錄元數據並保存到 “根目錄\cases\<case_id>\meta.json” 中。並且還會把改動涉及到的代碼文件（帶著文件夾路徑一起）複製進來。

- suspicious_collector.py：獨立腳本，用於根據不同的指標和閾值來搜索符合條件的 case_id 並且輸出。可以在裏面修改選擇你想要的 ratios 和閾值。

## D.測試腳本使用
 - 首先把你寫好的測試腳本放到 caseid 的文件夾內，命名爲 test.py
 - 然後在根目錄啓動終端，輸入:  python3.9 ./scripts/run_case_test.py --case <caseid> --patch <model/pr> --test-script test.py
 - model 或 pr 表示你想應用哪個 patch 進行測試。
 - 別的版本的 python 也許也行，但是有些庫比較老，可能必須用低版本的 python 才不會報錯。
 - 運行後去 caseid 文件夾裏面找到  runs 文件夾，裏面就是運行結果輸出等。
 
 
 
 
