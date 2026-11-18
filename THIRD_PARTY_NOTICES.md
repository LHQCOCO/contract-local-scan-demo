# 第三方组件与数据来源声明

Contract LocalScan 在开发中使用了以下开源组件与公开数据，感谢原作者。各组件版权归其各自作者所有，本声明不改变原许可证条款。

## 一、开源依赖

| 组件 | 许可证 | 用途 |
|---|---|---|
| Flask | BSD-3-Clause | Web 服务框架 |
| requests | Apache-2.0 | 访问本机 Ollama 接口 |
| jieba | MIT | 中文分词 |
| pypdf | BSD-3-Clause | PDF 文本解析 |
| python-docx | MIT | DOCX 解析与批注导出 |
| reportlab | BSD-3-Clause | PDF 报告生成 |
| openpyxl | MIT | Excel 报告生成 |
| waitress | MIT（类 BSD） | 生产级 WSGI 服务器 |
| pypdfium2 | Apache-2.0 / BSD-3 | PDF 页面渲染（OCR 前置） |
| rapidocr_onnxruntime | Apache-2.0 | 本地中文 OCR（含 onnxruntime，MIT；PP-OCR 系列模型，Apache-2.0） |

## 二、法规数据来源

1. **中国法律文本**（民法典、刑法、公司法等 1,468 部）
   - 来源：LawRefBook/Laws 社区整理的全国人大公布文本（github.com/LawRefBook/Laws），以及全国人大法律法规数据库（flk.npc.gov.cn）公开文本
   - 版权状态：依《著作权法》第五条，法律、法规官方文本不适用著作权保护
2. **CISG《联合国国际货物销售合同公约》**
   - 来源：UNCITRAL 官方英文文本；版权归联合国所有，此处复制仅作检索参考
3. **《承认及执行外国仲裁裁决公约》（纽约公约）**
   - 来源：UNCITRAL 官方文本；版权归联合国所有
4. **《国际商事仲裁示范法》（2006）**
   - 来源：UNCITRAL 官方文本；版权归联合国所有
5. **UNIDROIT《国际商事合同通则》（2016）**（中英文）
   - 来源：UNIDROIT 官网公开的 Black Letter 文本；版权归国际统一私法协会（UNIDROIT）所有
6. **Incoterms® 2020**
   - Incoterms® 为国际商会（ICC）注册商标。知识库中收录的 11 条释义为本工具编写方概括整理，**并非 ICC 官方文本**；正式使用请以 ICC 官方出版物为准

## 三、示例文件

`samples/` 目录中的合同示例均为虚构，公司名称、金额、编号不指向任何真实主体。

## 四、字体

OCR 测试用例在 macOS 构建环境中引用系统字体 PingFang SC（苹果公司版权），仅在构建时本地使用，不随软件分发。
