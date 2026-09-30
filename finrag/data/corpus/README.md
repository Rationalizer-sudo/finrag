# 语料目录说明

本目录用于存放**待入库的公开金融制度文档**。

## 数据来源要求（课题实施约束）

> 禁止使用企业敏感数据，测试数据从政府金融监管部门官网获取公开数据
> 或使用企业提供的公开版文档。

合规的公开数据来源：

| 来源 | 网址 | 内容 |
|---|---|---|
| 中国人民银行 | https://www.pbc.gov.cn | 部门规章、规范性文件、公告 |
| 国家金融监督管理总局 | https://www.nfra.gov.cn | 银行保险监管规则 |
| 中国证券监督管理委员会 | https://www.csrc.gov.cn | 证券期货规章、公告 |
| 中国政府网 · 政策文件库 | https://www.gov.cn/zhengce/ | 法律、行政法规、国务院文件 |
| 国家外汇管理局 | https://www.safe.gov.cn | 外汇管理规范性文件 |
| 上海证券交易所 / 深圳证券交易所 | https://www.sse.com.cn / https://www.szse.cn | 业务规则、自律监管指引 |
| 中国证券投资基金业协会 | https://www.amac.org.cn | 自律规则 |

## 目录约定

```
data/
├── corpus/     # 真实公开制度文件（验收用）  ← 把下载的文件放这里
└── demo/       # 合成演示语料（功能/压测用，非真实法规）
```

## 使用方法

1. 从上述官网下载制度文件（支持 `.txt` `.md` `.pdf` `.docx` 以及图片 `.png` `.jpg`）。
   扫描版 PDF 与图片需要本机安装 Tesseract OCR 才能解析。
2. 放入 `data/corpus/`（可建子目录，入库脚本会递归扫描）。
3. 执行入库：

```bash
python scripts/ingest.py --path data/corpus
```

4. 记录来源链接（强烈建议，用于条文溯源）：

```bash
python scripts/ingest.py --path data/corpus --source-url https://www.pbc.gov.cn/xxx
```

## 关于 `data/demo/` 的合成数据

在没有网络、暂时拿不到真实语料的开发阶段，可用以下命令快速构造
1000+ 知识点的演示语料，用于验证检索性能与响应时延：

```bash
python scripts/generate_demo_corpus.py --documents 60
python scripts/ingest.py --path data/demo
```

⚠️ 合成数据**不是真实法规原文**，仅用于功能与性能测试，
**不得用于合规判断**；课题验收应使用 `data/corpus/` 中的真实公开文件。

## 命名建议

为便于人工核对，建议保持「发文机关 + 年份 + 制度名」的命名习惯：

```
中国人民银行_2020_商业银行流动性风险管理办法.txt
证监会_2023_上市公司信息披露管理办法.pdf
```
