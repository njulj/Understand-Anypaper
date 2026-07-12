# Understand Anypaper

[English](README.md)

Understand Anypaper 帮助你阅读研究论文。它会把论文转换成一个交互式的 **Paper Argument Graph（论文论证图谱）**。

它不是再给你一份线性摘要，而是展示论文里的想法如何相互连接：作者提出了什么主张，为什么这个主张重要，作者如何实现它，又用哪些证据来支持它。

[Screencast_20260709_175345_github.webm](https://github.com/user-attachments/assets/a3382678-4e64-4f9a-a2ff-e11821fdd92c)

<img width="3394" height="1940" alt="image" src="https://github.com/user-attachments/assets/e02b1854-1b46-445f-b4ce-9d4fc2c457cc" />
<img width="3394" height="1940" alt="image" src="https://github.com/user-attachments/assets/c7ba7435-a302-467b-a1d4-1e36f54c3b95" />

## 为什么使用它？

研究论文难读，往往不是因为没有摘要，而是因为真正重要的论证逻辑分散在摘要、引言、方法、公式、图表、实验和参考文献里。Understand Anypaper 会围绕论文的核心贡献重新组织这些逻辑。

对于每个贡献，你可以查看：

- **为什么需要它**：相关动机、问题、研究空白和前人工作。
- **它如何实现**：方法、模块、公式、算法和图示。
- **它如何被证明**：数据集、指标、实验、消融、结果、表格和结论。

这能帮助你更快回答这些问题：

- 这篇论文真正的贡献是什么？
- 每个贡献分别由哪些证据支撑？
- 论文原文在哪里说了这件事？
- 哪些公式、图表或实验最关键？
- 它建立在哪些前人工作之上？
- 某个贡献是否证据充分，还是缺少关键支撑？

## 功能

- 上传论文并生成交互式论证图谱。
- 在同一界面中并排查看论文、图谱和节点详情。
- 点击图谱节点，跳回论文中的原始证据位置。
- 围绕单个贡献展开子图，而不是一次性阅读整篇论文。
- 将每个节点追溯到原文、页面位置和证据单元。
- 学习时搜索和过滤图谱。
- 当模型理解错误时，手动修正图谱。
- 保存已分析的论文，之后继续查看。

## 快速开始

推荐使用 [Devbox](https://github.com/jetify-com/devbox) 进行本地开发和运行。

```bash
cp .env.example .env
devbox services up
```

打开网页应用：

<http://localhost:5173>

生成图谱需要在 `.env` 中配置 LLM API key。
