# 论文识别归档软件

Python + PyQt5 写的论文 PDF 识别归档工具。支持拖入 PDF，点击“导入解析”后识别题目、作者、出版社/期刊/会议、发表时间，并把 PDF 与 `metadata.json` 存入默认 `archive` 文件夹。

## 环境

```powershell
conda env create -f environment.yml
conda activate lwenv
```

如果已经有 `lwenv`：

```powershell
conda activate lwenv
pip install -r requirements.txt
```

## 运行

```powershell
.\run_lwenv.ps1
```

或：

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n lwenv python paper_archiver.py
```

## 模型 API

软件内有三种服务选项：

- `DeepSeek API`
- `小米 MiMo Reasoning`
- `自定义 OpenAI兼容`

填写 API 地址、模型、API Key 和认证头后，可以先点“验证 API Key”。软件会发送一个很小的测试请求，用来确认 Key、Base URL、模型名和认证头是否匹配。

### DeepSeek

```powershell
$env:DEEPSEEK_API_KEY="你的 DeepSeek API Key"
$env:DEEPSEEK_MODEL="deepseek-chat"
.\run_lwenv.ps1
```

默认 API 地址：

```text
https://api.deepseek.com/chat/completions
```

### 小米 MiMo

软件已按你给的参数设置默认值：

```text
模型：mimo-v2.5-pro
Provider：custom:xiaomi-mimo-reasoning
Base URL：https://token-plan-cn.xiaomimimo.com/v1
```

配置 API Key 后运行：

```powershell
$env:XIAOMI_API_KEY="你的小米 MiMo API Key"
$env:XIAOMI_BASE_URL="https://token-plan-cn.xiaomimimo.com/v1"
$env:XIAOMI_MODEL="mimo-v2.5-pro"
$env:XIAOMI_AUTH_HEADER="api-key"
.\run_lwenv.ps1
```

程序会自动把 `/v1` 基础地址补成 `/v1/chat/completions`。如果你的接口要求 `Authorization: Bearer ...`，可以在界面里把“认证头”改成 `Authorization`。

### 网页兜底

API 调用失败或没有 API Key 时，可以点击“打开 DeepSeek 网页”。软件会把识别提示词复制到剪贴板。你在网页中获得 JSON 后，回到软件点击“粘贴 JSON 结果”。

要求模型返回的 JSON 格式：

```json
{
  "title": "论文题目",
  "authors": ["作者1", "作者2"],
  "publisher": "期刊/会议/出版社",
  "published_time": "发表时间"
}
```

## 归档结果

点击“归档到 archive”会默认保存到项目目录下的 `archive` 文件夹，也可以在界面中选择其他归档目录。

```text
archive/
  2024 First Author Paper Title/
    original.pdf
    metadata.json
```
