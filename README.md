# 图片下载器

从网页批量抓取并下载图片的桌面工具，基于 Python + CustomTkinter 构建。支持多线程并发下载、图片预览、高级筛选、下载历史等。

## 截图

启动后选择「下载」标签页，粘贴网页 URL 即可开始抓取。支持三个标签页切换：下载、历史记录、设置。

## 功能

- **批量网址** — 多行输入，每行一个 URL，自动按页面标题分文件夹存储
- **多线程下载** — 1~16 线程可调，大幅提升下载速度
- **图片预览** — 已下载图片显示缩略图网格，右键可打开文件/打开文件夹/复制路径
- **平台适配** — 内置 B站专栏 API 适配器，可注册更多平台专用解析器
- **高级筛选** — 按文件类型（gif/png/jpg/webp）、最小文件尺寸过滤
- **MD5 去重** — 自动跳过已下载的重复图片
- **自定义请求头** — User-Agent、Cookie、Referer 等自由配置
- **下载历史** — 每次下载自动记录，支持一键重新下载
- **主题切换** — 浅色 / 深色 / 跟随系统

## 安装

```bash
pip install customtkinter Pillow requests beautifulsoup4
```

或者：

```bash
pip install -r requirements.txt
```

## 使用

```bash
python dpya_gui.py
```

1. 在「下载」标签页输入网页 URL（每行一个）
2. 选择保存目录
3. 调整线程数和筛选选项
4. 点击「开始下载」

## 项目结构

```
├── dpya.py              # 原命令行版本（保留）
├── dpya_gui.py          # GUI 主程序入口
├── core/
│   ├── scraper.py       # 网页爬取 + 平台适配器
│   ├── downloader.py    # 多线程下载管理器
│   └── utils.py         # 去重、筛选、哈希工具
├── settings.json        # 用户设置（自动生成）
└── history.json         # 下载历史（自动生成）
```

## 扩展平台适配

在 `core/scraper.py` 中使用 `register_platform()` 注册新平台：

```python
from core.scraper import register_platform

def my_parser(url, headers):
    # 返回 (image_urls, page_title)
    return [...], "title"

register_platform(r'example\.com', my_parser)
```

## 依赖

- Python 3.10+
- customtkinter — 现代化 GUI 框架
- Pillow — 图片缩略图
- requests — HTTP 请求
- beautifulsoup4 — HTML 解析
