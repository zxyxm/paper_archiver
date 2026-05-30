# 论文识别归档软件

Python + PyQt5 写的论文 PDF 识别、批量导入和归档工具。支持调用 OpenAI 兼容接口抽取论文信息、摘要和大白话说明，并将 PDF 与 `metadata.json` 保存到 `archive` 目录。

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

## 主要功能

- 保存 API Key：在界面填写服务、API 地址、模型、API Key、认证头后点击“保存 API Key”，配置会写入 `config.json`。
- 批量导入 PDF：支持选择多个 PDF 或拖入多个 PDF，解析后返回总数和“是否为论文”的判断。
- 论文信息编辑：每篇论文可用“上一篇/下一篇”切换，信息区、摘要区和笔记区直接编辑。
- 保存论文信息：点击“保存论文信息”会把当前论文信息、摘要和笔记写入对应 `metadata.json`。
- 中文翻译：论文题目、作者和期刊/会议/出版社增加中文字段。
- Google Scholar：点击“Google Scholar 主页”会尝试查找作者主页，找到后跳转，找不到会弹窗提示。
- 归档日志：点击“日志”可查看 archive 内论文记录、PDF、JSON、笔记数量等统计。
- 人工笔记：在“人工笔记”里编辑后点击“保存人工笔记”，会写入对应 `metadata.json`。
- 去重：归档时会按 PDF 哈希和题目去重；重复论文不会新增目录，会读取旧 JSON 和之前保存的人工笔记。
- 其他页面：可查看当前论文的 JSON 内容，以及发送给大模型的 system/user 交互提示词。
- 论文检索页面：可按期刊名查询 LetPub 公开页面中的中科院分区、JCR/WOS 分区和来源链接。

## 归档结构

```text
archive/
  2024 First Author Paper Title/
    original.pdf
    metadata.json
```

`metadata.json` 会保存论文信息、摘要、大白话、模型提示词、人工笔记、PDF 哈希和源文件路径。
