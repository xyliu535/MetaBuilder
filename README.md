# MetaBuilder
## A.使用方法
- 进入 leaderboard 官网，下滑进入“Analyze Results in Detail”，挑选想要研究的模型，点击 load data 并点击 (logs) 进入其 github 项目页面，找到 “metadata.yaml” ，里面的 assets 下会给出这个模型产生的 logs 的下载链接，下载至（项目根目录\Model_data\）即可。
- 此外，还需要手动把该模型对应 github 项目中的 “results\results.json” 也下载到（项目根目录\Model_data\<Model_name>）里面。results.json 记录的是这个模型通过测试的 case id ，也就是我们的研究对象。
- 安装依赖：
    ```sh
    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    ```
- 运行脚本：
    ```sh
    python ./scripts/meta_builder.py
    ```
- 运行完毕后会在项目根目录下生成 “cases/” 和 “repos/” 两个文件夹，分别保存 “所有的cases的patch信息和元数据等” 和 “cases用到的项目”
- 人工审计案例从 "cases/" 中选取即可。
- 如果需要根据 ratios 来筛选 case id ，可以打开 suspicious_collector.py 修改一下需要的指标和阈值，然后执行脚本：
    ```sh
    python ./scripts/suspicious_collector.py
    ```

## B.脚本结构
- 项目结构：
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
## C.脚本功能介绍：
- patch_collector.py：把 "Model_data/<model_name>/logs/" 里面的所有 cases 复制到 "根目录/cases/" 里面，并且联网从 SWE-Bench Verified 的数据集中把每个 case 对应的人工 patch 下载到  "根目录/cases/" 对应的文件夹里。

- file_filter.py：对模型 patch 以及人工 patch 改动涉及的文件进行分类，分为：代码文件（CODE），测试文件 （TEST），文档文件（DOC）和垃圾文件（TRASH）。我们主要对“代码文件”进行研究，划分依据主要是文件名称以及路径名称，后续也可以对划分依据进行一定修改完善。

- repo_maintainer.py：联网把所有 cases 涉及的 Python 库下载到 “根目录/repos/” 中，并且后续可以针对每个 case 当时的 base_commit 来进行 git checkout 到当时的分支。

- comparison_analyzer.py：对每个 case 分析模型 patch 与人工 patch 的差别数据并且记录。

- symbol_locator.py：为每个 case 的模型 patch 和人工 patch 定位 “改动所位于的类/方法”，并且记录。

- ratios_computer.py：利用现有信息进行一些 ratios 的计算，并且记录。后续要对指标计算进行修改就可以直接修改此脚本。

- meta_builder.py：结合上述脚本，为每个 case 计算、记录元数据并保存到 “根目录\cases\\<case_id>\meta.json” 中。

- suspicious_collector.py：独立脚本，用于根据不同的指标和阈值来搜索符合条件的 case_id 并且输出。可以在里面修改选择你想要的 ratios 和阈值。




